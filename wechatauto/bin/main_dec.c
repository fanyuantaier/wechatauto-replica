/* silkdec.c — 独立 SILK v3 解码器（基于 pilk 0.2.4 内置的 Silk SDK 解码循环）。
 * 用法: silkdec <in.silk> <out.pcm> [API_Fs_Hz]
 * 兼容两种流头: 02 23 21 53 49 4c 4b 5f 56 33（微信变体，\x02#!SILK_V3）
 *           与裸流 "#!SILK_V3"。
 * 输出: 16bit 小端 PCM（默认 24000Hz 单声道）。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "SKP_Silk_SDK_API.h"
#include "SKP_Silk_SigProc_FIX.h"

#define DECODE_MAX_BYTES_PER_FRAME     1024
#define MAX_INPUT_FRAMES        5
#define MAX_FRAME_LENGTH        480
#define FRAME_LENGTH_MS         20
#define MAX_API_FS_KHZ          48
#define MAX_LBRR_DELAY          2

static SKP_int32 rand_seed = 1;

int main(int argc, char *argv[]) {
    size_t counter;
    SKP_int32 totPackets, i, k;
    SKP_int16 ret, len, tot_len;
    SKP_int16 nBytes;
    SKP_uint8 payload[DECODE_MAX_BYTES_PER_FRAME * MAX_INPUT_FRAMES * (MAX_LBRR_DELAY + 1)];
    SKP_uint8 *payloadEnd = NULL, *payloadToDec = NULL;
    SKP_uint8 FECpayload[DECODE_MAX_BYTES_PER_FRAME * MAX_INPUT_FRAMES], *payloadPtr;
    SKP_int16 nBytesFEC;
    SKP_int16 nBytesPerPacket[MAX_LBRR_DELAY + 1], totBytes;
    SKP_int16 out[((FRAME_LENGTH_MS * MAX_API_FS_KHZ) << 1) * MAX_INPUT_FRAMES], *outPtr;
    FILE *bitInFile, *speechOutFile;
    SKP_int32 packetSize_ms = 0, API_Fs_Hz = 0;
    SKP_int32 decSizeBytes;
    void *psDec;
    SKP_float loss_prob;
    SKP_int32 frames, lost;
    SKP_SILK_SDK_DecControlStruct DecControl;
    double loss_prob_d = 0.0;
    double file_duration_ms = 0.0;

    if (argc < 3) {
        fprintf(stderr, "usage: %s <in.silk> <out.pcm> [API_Fs_Hz]\n", argv[0]);
        return 1;
    }
    if (argc > 3) API_Fs_Hz = atoi(argv[3]);

    loss_prob = 0.0f;

    bitInFile = fopen(argv[1], "rb");
    if (bitInFile == NULL) {
        fprintf(stderr, "Error: could not open input file %s\n", argv[1]);
        return 1;
    }

    /* Check Silk header：兼容 02#!SILK_V3 与裸 #!SILK_V3 */
    {
        char hdr[10];
        size_t n = fread(hdr, 1, 10, bitInFile);
        if (n >= 10 && hdr[0] == (char)0x02 &&
            memcmp(hdr + 1, "#!SILK_V3", 9) == 0) {
            /* 微信变体：前缀已消费 */
        } else if (n >= 9 && memcmp(hdr, "#!SILK_V3", 9) == 0) {
            /* 裸流：多读了一个字节，回退 */
            fseek(bitInFile, -1, SEEK_CUR);
        } else {
            fprintf(stderr, "Error: Wrong Silk header (got %02x %02x %02x)\n",
                    n > 0 ? (unsigned char)hdr[0] : 0,
                    n > 1 ? (unsigned char)hdr[1] : 0,
                    n > 2 ? (unsigned char)hdr[2] : 0);
            return 1;
        }
    }

    speechOutFile = fopen(argv[2], "wb");
    if (speechOutFile == NULL) {
        fprintf(stderr, "Error: could not open output file %s\n", argv[2]);
        return 1;
    }

    /* Set the samplingrate that is requested for the output */
    DecControl.API_sampleRate = (API_Fs_Hz == 0) ? 24000 : API_Fs_Hz;
    DecControl.framesPerPacket = 1;

    SKP_Silk_SDK_Get_Decoder_Size(&decSizeBytes);
    psDec = malloc(decSizeBytes);
    SKP_Silk_SDK_InitDecoder(psDec);

    totPackets = 0;
    payloadEnd = payload;

    for (i = 0; i < MAX_LBRR_DELAY; i++) {
        counter = fread(&nBytes, sizeof(SKP_int16), 1, bitInFile);
        counter = fread(payloadEnd, sizeof(SKP_uint8), nBytes, bitInFile);
        if ((SKP_int16) counter < nBytes) break;
        nBytesPerPacket[i] = nBytes;
        payloadEnd += nBytes;
        totPackets++;
    }

    while (1) {
        counter = fread(&nBytes, sizeof(SKP_int16), 1, bitInFile);
        if (nBytes < 0 || counter < 1) break;
        counter = fread(payloadEnd, sizeof(SKP_uint8), nBytes, bitInFile);
        if ((SKP_int16) counter < nBytes) break;

        rand_seed = SKP_RAND(rand_seed);
        if ((((float) ((rand_seed >> 16) + (1 << 15))) / 65535.0f >= (loss_prob / 100.0f)) && (counter > 0)) {
            nBytesPerPacket[MAX_LBRR_DELAY] = nBytes;
            payloadEnd += nBytes;
        } else {
            nBytesPerPacket[MAX_LBRR_DELAY] = 0;
        }

        if (nBytesPerPacket[0] == 0) {
            lost = 1;
            payloadPtr = payload;
            for (i = 0; i < MAX_LBRR_DELAY; i++) {
                if (nBytesPerPacket[i + 1] > 0) {
                    SKP_Silk_SDK_search_for_LBRR(payloadPtr, nBytesPerPacket[i + 1], (i + 1), FECpayload, &nBytesFEC);
                    if (nBytesFEC > 0) {
                        payloadToDec = FECpayload;
                        nBytes = nBytesFEC;
                        lost = 0;
                        break;
                    }
                }
                payloadPtr += nBytesPerPacket[i + 1];
            }
        } else {
            lost = 0;
            nBytes = nBytesPerPacket[0];
            payloadToDec = payload;
        }

        outPtr = out;
        tot_len = 0;

        if (lost == 0) {
            frames = 0;
            do {
                SKP_Silk_SDK_Decode(psDec, &DecControl, 0, payloadToDec, nBytes, outPtr, &len);
                frames++;
                outPtr += len;
                tot_len += len;
                if (frames > MAX_INPUT_FRAMES) {
                    outPtr = out;
                    tot_len = 0;
                    frames = 0;
                }
            } while (DecControl.moreInternalDecoderFrames);
        } else {
            for (i = 0; i < DecControl.framesPerPacket; i++) {
                SKP_Silk_SDK_Decode(psDec, &DecControl, 1, payloadToDec, nBytes, outPtr, &len);
                outPtr += len;
                tot_len += len;
            }
        }

        packetSize_ms = tot_len / (DecControl.API_sampleRate / 1000);
        totPackets++;
        file_duration_ms += (double)tot_len * 1000.0 / (double)DecControl.API_sampleRate;

        fwrite(out, sizeof(SKP_int16), tot_len, speechOutFile);

        totBytes = 0;
        for (i = 0; i < MAX_LBRR_DELAY; i++) {
            totBytes += nBytesPerPacket[i + 1];
        }
        if (totBytes < 0 || totBytes > (SKP_int16)sizeof(payload)) {
            fprintf(stderr, "Error: packet buffer overflow after %d packets\n", totPackets);
            fclose(speechOutFile);
            return 1;
        }
        SKP_memmove(payload, &payload[nBytesPerPacket[0]], totBytes * sizeof(SKP_uint8));
        payloadEnd -= nBytesPerPacket[0];
        SKP_memmove(nBytesPerPacket, &nBytesPerPacket[1], MAX_LBRR_DELAY * sizeof(SKP_int16));
    }

    /* Empty the recieve buffer */
    for (k = 0; k < MAX_LBRR_DELAY; k++) {
        if (nBytesPerPacket[0] == 0) {
            lost = 1;
            payloadPtr = payload;
            for (i = 0; i < MAX_LBRR_DELAY - k; i++) {
                if (nBytesPerPacket[i + 1] > 0) {
                    SKP_Silk_SDK_search_for_LBRR(payloadPtr, nBytesPerPacket[i + 1], (i + 1), FECpayload, &nBytesFEC);
                    if (nBytesFEC > 0) {
                        payloadToDec = FECpayload;
                        nBytes = nBytesFEC;
                        lost = 0;
                        break;
                    }
                }
                payloadPtr += nBytesPerPacket[i + 1];
            }
        } else {
            lost = 0;
            nBytes = nBytesPerPacket[0];
            payloadToDec = payload;
        }

        outPtr = out;
        tot_len = 0;
        if (lost == 0) {
            frames = 0;
            do {
                SKP_Silk_SDK_Decode(psDec, &DecControl, lost, payloadToDec, nBytes, outPtr, &len);
                frames++;
                outPtr += len;
                tot_len += len;
                if (frames > MAX_INPUT_FRAMES) {
                    outPtr = out;
                    tot_len = 0;
                    frames = 0;
                }
            } while (DecControl.moreInternalDecoderFrames);
        } else {
            for (i = 0; i < DecControl.framesPerPacket; i++) {
                SKP_Silk_SDK_Decode(psDec, &DecControl, 1, payloadToDec, nBytes, outPtr, &len);
                outPtr += len;
                tot_len += len;
            }
        }
        file_duration_ms += (double)tot_len * 1000.0 / (double)DecControl.API_sampleRate;
        fwrite(out, sizeof(SKP_int16), tot_len, speechOutFile);
    }

    fclose(speechOutFile);
    ret = 0;
    fprintf(stderr, "OK: packets=%d frames_duration=%.0f ms API_Fs=%d Hz\n",
            totPackets, file_duration_ms, DecControl.API_sampleRate);
    free(psDec);
    (void)packetSize_ms; (void)ret;
    return 0;
}
