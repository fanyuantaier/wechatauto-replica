"""微信界面文案的多语言配置表。

每一项均为 ``{键: {'cn': ..., 'cn_t': ..., 'en': ...}}`` 结构，
通过 :func:`get_lang` 依据 :attr:`wechatauto.param.WxParam.LANGUAGE`
取值，未命中时回退到简体中文，再回退到键本身。
"""

from wechatauto.param import WxParam


def get_lang(table: dict, key: str) -> str:
    item = table.get(key)
    if not isinstance(item, dict):
        return key
    text = item.get(getattr(WxParam, 'LANGUAGE', 'cn'))
    if text:
        return text
    text = item.get('cn')
    return text if text else key


WECHAT_MAIN = {
    '新的朋友': {'cn': '新的朋友', 'cn_t': '', 'en': ''},
    '添加朋友': {'cn': '添加朋友', 'cn_t': '', 'en': ''},
    '搜索结果': {'cn': '搜索：', 'cn_t': '', 'en': ''},
    '发起群聊': {'cn': '发起群聊', 'cn_t': '', 'en': ''},
    '找不到相关账号或内容': {'cn': '找不到相关账号或内容', 'cn_t': '', 'en': ''},
}

WECHAT_NAVIGATION_BOX = {
    '聊天': {'cn': '聊天', 'cn_t': '聊天', 'en': 'Chats'},
    '通讯录': {'cn': '通讯录', 'cn_t': '通訊錄', 'en': 'Contacts'},
    '收藏': {'cn': '收藏', 'cn_t': '收藏', 'en': 'Favorites'},
    '聊天文件': {'cn': '聊天文件', 'cn_t': '聊天文件', 'en': 'Files'},
    '朋友圈': {'cn': '朋友圈', 'cn_t': '朋友圈', 'en': 'Moments'},
    '搜一搜': {'cn': '搜一搜', 'cn_t': '搜一搜', 'en': 'Search'},
    '视频号': {'cn': '视频号', 'cn_t': '視頻號', 'en': 'Channels'},
    '看一看': {'cn': '看一看', 'cn_t': '看一看', 'en': 'Top Stories'},
    '小程序面板': {'cn': '小程序面板', 'cn_t': '小程序面板', 'en': 'Mini Programs'},
    '手机': {'cn': '手机', 'cn_t': '手機', 'en': 'Phone'},
    '设置及其他': {'cn': '设置及其他', 'cn_t': '設置及其他', 'en': 'Settings'},
    '更多': {'cn': '更多', 'cn_t': '更多', 'en': 'More'},
}

WECHAT_CHAT_BOX = {
    '查看更多消息': {'cn': '查看更多消息', 'cn_t': '', 'en': ''},
    '消息': {'cn': '消息', 'cn_t': '', 'en': ''},
    '表情': {'cn': '表情(Alt+E)', 'cn_t': '', 'en': ''},
    '发送文件': {'cn': '发送文件', 'cn_t': '', 'en': ''},
    '截图': {'cn': '截图', 'cn_t': '', 'en': ''},
    '聊天记录': {'cn': '聊天记录', 'cn_t': '', 'en': ''},
    '语音聊天': {'cn': '语音聊天', 'cn_t': '', 'en': ''},
    '视频聊天': {'cn': '视频聊天', 'cn_t': '', 'en': ''},
    '聊天信息': {'cn': '聊天信息', 'cn_t': '', 'en': ''},
    '发送': {'cn': '发送(S)', 'cn_t': '', 'en': ''},
    '置顶': {'cn': '置顶', 'cn_t': '', 'en': ''},
    '最小化': {'cn': '最小化', 'cn_t': '', 'en': ''},
    '最大化': {'cn': '最大化', 'cn_t': '', 'en': ''},
    '关闭': {'cn': '关闭', 'cn_t': '', 'en': ''},
    '多选': {'cn': '多选', 'cn_t': '', 'en': ''},
    '以下为新消息': {'cn': '以下为新消息', 'cn_t': '', 'en': ''},
    're_新消息按钮': {'cn': r'.*?条新消息', 'cn_t': '', 'en': ''},
}

WECHAT_SESSION_BOX = {
    # 聊天页面
    '聊天记录': {'cn': '聊天记录', 'cn_t': '', 'en': ''},
    '会话': {'cn': '会话', 'cn_t': '', 'en': ''},
    '已置顶': {'cn': '已置顶', 'cn_t': '', 'en': ''},
    '文件传输助手': {'cn': '文件传输助手', 'cn_t': '', 'en': ''},
    '折叠的群聊': {'cn': '折叠的群聊', 'cn_t': '', 'en': ''},
    '折叠置顶聊天': {'cn': '折叠置顶聊天', 'cn_t': '', 'en': ''},
    '发起群聊': {'cn': '发起群聊', 'cn_t': '', 'en': ''},
    '搜索': {'cn': '搜索', 'cn_t': '', 'en': ''},
    're_条数': {'cn': r'\[\d+条\]', 'cn_t': '', 'en': ''},
    're_置顶聊天': {'cn': r'.*?个置顶聊天', 'cn_t': '', 'en': ''},

    # 联系人页面
    '添加朋友': {'cn': '添加朋友', 'cn_t': '', 'en': ''},
    '联系人': {'cn': '联系人', 'cn_t': '', 'en': ''},
    '通讯录管理': {'cn': '通讯录管理', 'cn_t': '', 'en': ''},
    '新的朋友': {'cn': '新的朋友', 'cn_t': '', 'en': ''},
    '公众号': {'cn': '公众号', 'cn_t': '', 'en': ''},
    '企业号': {'cn': '企业号', 'cn_t': '', 'en': ''},
    '群聊': {'cn': '群聊', 'cn_t': '', 'en': ''},

    # 收藏页面
    '分类': {'cn': '分类', 'cn_t': '', 'en': ''},
    '新建笔记': {'cn': '新建笔记', 'cn_t': '', 'en': ''},
    '全部收藏': {'cn': '全部收藏', 'cn_t': '', 'en': ''},
    '最近使用': {'cn': '最近使用', 'cn_t': '', 'en': ''},
    '链接': {'cn': '链接', 'cn_t': '', 'en': ''},
    '图片与视频': {'cn': '图片与视频', 'cn_t': '', 'en': ''},
    '笔记': {'cn': '笔记', 'cn_t': '', 'en': ''},
    '文件': {'cn': '文件', 'cn_t': '', 'en': ''},
    '分割线': {'cn': '分割线', 'cn_t': '', 'en': ''},
    '展开标签': {'cn': '展开标签', 'cn_t': '', 'en': ''},
    '折叠标签': {'cn': '折叠标签', 'cn_t': '', 'en': ''},
    '标签': {'cn': '标签', 'cn_t': '', 'en': ''},
}

MESSAGES = {
    '[图片]': {'cn': '[图片]', 'cn_t': '', 'en': ''},
    '[视频]': {'cn': '[视频]', 'cn_t': '', 'en': ''},
    '[语音]': {'cn': '[语音]', 'cn_t': '', 'en': ''},
    '[音乐]': {'cn': '[音乐]', 'cn_t': '', 'en': ''},
    '[位置]': {'cn': '[位置]', 'cn_t': '', 'en': ''},
    '[链接]': {'cn': '[链接]', 'cn_t': '', 'en': ''},
    '[文件]': {'cn': '[文件]', 'cn_t': '', 'en': ''},
    '[名片]': {'cn': '[名片]', 'cn_t': '', 'en': ''},
    '[笔记]': {'cn': '[笔记]', 'cn_t': '', 'en': ''},
    '[视频号]': {'cn': '[视频号]', 'cn_t': '', 'en': ''},
    '[动画表情]': {'cn': '[动画表情]', 'cn_t': '', 'en': ''},
    '[聊天记录]': {'cn': '[聊天记录]', 'cn_t': '', 'en': ''},
    '微信转账': {'cn': '微信转账', 'cn_t': '', 'en': ''},
    '接收中': {'cn': '接收中', 'cn_t': '', 'en': ''},
    're_语音': {'cn': r'^\[语音\]\d+秒(,未播放)?$', 'cn_t': '', 'en': ''},
    're_引用消息': {'cn': r'(^.+)\n引用.*?的消息 : (.+$)', 'cn_t': '', 'en': ''},
    're_拍一拍': {'cn': r'^.+拍了拍.+$', 'cn_t': '', 'en': ''},
}

MENU_OPTIONS = {
    # session
    '置顶': {'cn': '置顶', 'cn_t': '置頂', 'en': 'Pin'},
    '取消置顶': {'cn': '取消置顶', 'cn_t': '取消置頂', 'en': 'Unpin'},
    '标为未读': {'cn': '标为未读', 'cn_t': '標為未讀', 'en': 'Mark as unread'},
    '消息免打扰': {'cn': '消息免打扰', 'cn_t': '消息免打擾', 'en': 'Mute'},
    '在独立窗口打开': {'cn': '在独立窗口打开', 'cn_t': '在獨立窗口打開', 'en': 'Open in separate window'},
    '不显示聊天': {'cn': '不显示聊天', 'cn_t': '不顯示聊天', 'en': 'Hide chat'},
    '删除聊天': {'cn': '删除聊天', 'cn_t': '刪除聊天', 'en': 'Delete chat'},

    # msg
    '撤回': {'cn': '撤回', 'cn_t': '撤回', 'en': 'Recall'},
    '复制': {'cn': '复制', 'cn_t': '複製', 'en': 'Copy'},
    '放大阅读': {'cn': '放大阅读', 'cn_t': '放大閱讀', 'en': 'Enlarge'},
    '翻译': {'cn': '翻译', 'cn_t': '翻譯', 'en': 'Translate'},
    '转发': {'cn': '转发...', 'cn_t': '轉發...', 'en': 'Forward...'},
    '收藏': {'cn': '收藏', 'cn_t': '收藏', 'en': 'Favorite'},
    '多选': {'cn': '多选', 'cn_t': '多選', 'en': 'Select multiple'},
    '引用': {'cn': '引用', 'cn_t': '引用', 'en': 'Quote'},
    '搜一搜': {'cn': '搜一搜', 'cn_t': '搜一搜', 'en': 'Search'},
    '删除': {'cn': '删除', 'cn_t': '刪除', 'en': 'Delete'},
    '编辑': {'cn': '编辑', 'cn_t': '編輯', 'en': 'Edit'},
    '另存为': {'cn': '另存为...', 'cn_t': '另存為...', 'en': 'Save as...'},
    '语音转文字': {'cn': '语音转文字', 'cn_t': '語音轉文字', 'en': 'Transcribe'},
    '在文件夹中显示': {'cn': '在文件夹中显示', 'cn_t': '在文件夾中顯示', 'en': 'Show in folder'},

    # edit
    '剪切': {'cn': '剪切', 'cn_t': '剪切', 'en': 'Cut'},
    '粘贴': {'cn': '粘贴', 'cn_t': '粘貼', 'en': 'Paste'},
}

MOMENTS = {
    '朋友圈': {'cn': '朋友圈', 'cn_t': '朋友圈', 'en': 'Moments'},
    '刷新': {'cn': '刷新', 'cn_t': '刷新', 'en': 'Refresh'},
    '评论': {'cn': '评论', 'cn_t': '評論', 'en': 'Comment'},
    '评论区': {'cn': '评论区', 'cn_t': '評論區', 'en': 'Comments'},
    '广告': {'cn': '广告', 'cn_t': '廣告', 'en': 'Advertisement'},
    '赞': {'cn': '赞', 'cn_t': '讚', 'en': 'Like'},
    '取消': {'cn': '取消', 'cn_t': '取消', 'en': 'Cancel'},
    '发送': {'cn': '发送', 'cn_t': '發送', 'en': 'Send'},
    '分隔符_点赞': {'cn': '，', 'cn_t': '，', 'en': ', '},
    're_图片数': {'cn': r'包含\d+张图片', 'cn_t': r'包含\d+張圖片', 'en': r'Contains \d+ photos'},
}

MOMENT_PRIVACY = {
    '谁可以看': {'cn': '谁可以看', 'cn_t': '誰可以看', 'en': 'Who can see'},
    '公开': {'cn': '公开', 'cn_t': '公開', 'en': 'Public'},
    '所有朋友可见': {'cn': '所有朋友可见', 'cn_t': '所有朋友可見', 'en': 'All friends'},
    '私密': {'cn': '私密', 'cn_t': '私密', 'en': 'Private'},
    '仅自己可见': {'cn': '仅自己可见', 'cn_t': '僅自己可見', 'en': 'Only me'},
    '白名单': {'cn': '谁可以看', 'cn_t': '誰可以看', 'en': 'Selected friends'},
    '黑名单': {'cn': '不给谁看', 'cn_t': '不給誰看', 'en': 'Exclude friends'},
    '完成': {'cn': '完成', 'cn_t': '完成', 'en': 'Done'},
    '确定': {'cn': '确定', 'cn_t': '確定', 'en': 'OK'},
    '取消': {'cn': '取消', 'cn_t': '取消', 'en': 'Cancel'},
}

IMAGE_WINDOW = {
    '上一张': {'cn': '上一张', 'cn_t': '上一張', 'en': 'Previous'},
    '下一张': {'cn': '下一张', 'cn_t': '下一張', 'en': 'Next'},
    '预览': {'cn': '预览', 'cn_t': '預覽', 'en': 'Preview'},
    '放大': {'cn': '放大', 'cn_t': '放大', 'en': 'Zoom'},
    '缩小': {'cn': '缩小', 'cn_t': '縮小', 'en': 'Shrink'},
    '图片原始大小': {'cn': '图片原始大小', 'cn_t': '圖片原始大小', 'en': 'Original size'},
    '旋转': {'cn': '旋转', 'cn_t': '旋轉', 'en': 'Rotate'},
    '编辑': {'cn': '编辑', 'cn_t': '編輯', 'en': 'Edit'},
    '翻译': {'cn': '翻译', 'cn_t': '翻譯', 'en': 'Translate'},
    '提取文字': {'cn': '提取文字', 'cn_t': '提取文字', 'en': 'Extract text'},
    '识别图中二维码': {'cn': '识别图中二维码', 'cn_t': '識別圖中QR Code', 'en': 'Extract QR Code'},
    '另存为': {'cn': '另存为...', 'cn_t': '另存為...', 'en': 'Save as...'},
    '更多': {'cn': '更多', 'cn_t': '更多', 'en': 'More'},
    '复制': {'cn': '复制', 'cn_t': '複製', 'en': 'Copy'},
}

NEW_FRIEND_ELEMENT = {
    '新的朋友': {'cn': '新的朋友', 'cn_t': '新的朋友', 'en': 'New friends'},
    '回复': {'cn': '回复', 'cn_t': '回覆', 'en': 'Reply'},
    '发送': {'cn': '发送', 'cn_t': '發送', 'en': 'Send'},
    '朋友圈': {'cn': '朋友圈', 'cn_t': '朋友圈', 'en': 'Moments'},
    '仅聊天': {'cn': '仅聊天', 'cn_t': '僅聊天', 'en': 'Chat only'},
    '聊天、朋友圈、微信运动等': {
        'cn': '聊天、朋友圈、微信运动等',
        'cn_t': '聊天、朋友圈、微信運動等',
        'en': 'Chats, Moments, WeRun, etc.',
    },
    '备注名': {'cn': '备注名', 'cn_t': '備註名', 'en': 'Alias'},
    '标签': {'cn': '标签', 'cn_t': '標籤', 'en': 'Tags'},
}

PROFILE_WINDOW = {
    '微信号': {'cn': '微信号：', 'cn_t': '微信號：', 'en': 'WeChat ID: '},
    '昵称': {'cn': '昵称：', 'cn_t': '暱稱：', 'en': 'Nickname: '},
    '地区': {'cn': '地区：', 'cn_t': '地區：', 'en': 'Region: '},
    '个性签名': {'cn': '个性签名', 'cn_t': '個性簽名', 'en': 'Signature'},
    '来源': {'cn': '来源', 'cn_t': '來源', 'en': 'Source'},
    '备注': {'cn': '备注', 'cn_t': '備註', 'en': 'Alias'},
    '共同群聊': {'cn': '共同群聊', 'cn_t': '共同群聊', 'en': 'Common groups'},
    '添加到通讯录': {'cn': '添加到通讯录', 'cn_t': '添加到通訊錄', 'en': 'Add to contacts'},
    '更多': {'cn': '更多', 'cn_t': '更多', 'en': 'More'},
}

WECHAT_BROWSER = {
    '关闭': {'cn': '关闭', 'cn_t': '關閉', 'en': 'Close'},
    '更多': {'cn': '更多', 'cn_t': '更多', 'en': 'More'},
    '地址和搜索栏': {'cn': '地址和搜索栏', 'cn_t': '地址和搜索欄', 'en': 'Address and search bar'},
    '转发给朋友': {'cn': '转发给朋友', 'cn_t': '轉發給朋友', 'en': 'Forward to friend'},
    '复制链接': {'cn': '复制链接', 'cn_t': '複製鏈接', 'en': 'Copy link'},
}

CHATROOM_DETAIL_WINDOW = {
    '聊天信息': {'cn': '聊天信息', 'cn_t': '聊天信息', 'en': 'Chat info'},
    '查看更多': {'cn': '查看更多', 'cn_t': '查看更多', 'en': 'View more'},
    '群聊名称': {'cn': '群聊名称', 'cn_t': '群聊名稱', 'en': 'Group name'},
    '仅群主或管理员可以修改': {'cn': '仅群主或管理员可以修改', 'cn_t': '僅群主或管理員可以修改', 'en': 'Only owner or admins can edit'},
    '我在本群的昵称': {'cn': '我在本群的昵称', 'cn_t': '我在本群的暱稱', 'en': 'My nickname in group'},
    '仅群主和管理员可编辑': {'cn': '仅群主和管理员可编辑', 'cn_t': '僅群主和管理員可編輯', 'en': 'Only owner and admins can edit'},
    '点击编辑群公告': {'cn': '点击编辑群公告', 'cn_t': '點擊編輯群公告', 'en': 'Tap to edit announcement'},
    '编辑': {'cn': '编辑', 'cn_t': '編輯', 'en': 'Edit'},
    '备注': {'cn': '备注', 'cn_t': '備註', 'en': 'Alias'},
    '群公告': {'cn': '群公告', 'cn_t': '群公告', 'en': 'Announcement'},
    '完成': {'cn': '完成', 'cn_t': '完成', 'en': 'Done'},
    '发布': {'cn': '发布', 'cn_t': '發佈', 'en': 'Publish'},
    '退出群聊': {'cn': '退出群聊', 'cn_t': '退出群聊', 'en': 'Leave group'},
    '聊天成员': {'cn': '聊天成员', 'cn_t': '聊天成員', 'en': 'Members'},
    '添加': {'cn': '添加', 'cn_t': '添加', 'en': 'Add'},
    '移出': {'cn': '移出', 'cn_t': '移出', 'en': 'Remove'},
}

ADD_NEW_FRIEND_WINDOW = {
    '标签': {'cn': '标签', 'cn_t': '標籤', 'en': 'Tags'},
    '确定': {'cn': '确定', 'cn_t': '確定', 'en': 'OK'},
    '备注名': {'cn': '备注名', 'cn_t': '備註名', 'en': 'Alias'},
    '朋友圈': {'cn': '朋友圈', 'cn_t': '朋友圈', 'en': 'Moments'},
    '仅聊天': {'cn': '仅聊天', 'cn_t': '僅聊天', 'en': 'Chat only'},
    '聊天、朋友圈、微信运动等': {
        'cn': '聊天、朋友圈、微信运动等',
        'cn_t': '聊天、朋友圈、微信運動等',
        'en': 'Chats, Moments, WeRun, etc.',
    },
    '你的联系人较多，添加新的朋友时需选择权限': {
        'cn': '你的联系人较多，添加新的朋友时需选择权限',
        'cn_t': '你的聯繫人較多，添加新的朋友時需選擇權限',
        'en': 'You have many contacts, choose permissions when adding friends',
    },
    '发送添加朋友申请': {'cn': '发送添加朋友申请', 'cn_t': '發送添加朋友申請', 'en': 'Send friend request'},
}

ADD_GROUP_MEMBER_WINDOW = {
    '搜索': {'cn': '搜索', 'cn_t': '搜索', 'en': 'Search'},
    '确定': {'cn': '确定', 'cn_t': '確定', 'en': 'OK'},
    '完成': {'cn': '完成', 'cn_t': '完成', 'en': 'Done'},
    '发送': {'cn': '发送', 'cn_t': '發送', 'en': 'Send'},
    '已选择联系人': {'cn': '已选择联系人', 'cn_t': '已選擇聯繫人', 'en': 'Selected contacts'},
    '请勾选需要添加的联系人': {
        'cn': '请勾选需要添加的联系人',
        'cn_t': '請勾選需要添加的聯繫人',
        'en': 'Please select contacts to add',
    },
}
