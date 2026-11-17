# 冷月系—并行记忆分支

让不同对话拥有各自的记忆。聊过的事情可以自动整理，之后需要时再找回来。

## 可以做什么

- 为不同来源建立独立记忆空间，避免混在一起。
- 自动或手动整理聊天内容，查看记忆之间的联系。
- 整理结果不合适时，可以回到之前的记忆节点。

## 如何使用

在 AstrBot 中安装本插件，然后从左侧扩展功能打开「（月）并行记忆分支」，按面板提示完成设置。

已有的 B站、小黑盒公共记忆也由这里管理。

搭配[自然意图分析](https://github.com/YepStar/astrbot_plugin_intent_bridge)，可以用日常说法提出需求，由机器人调用已接入的功能，无须记住指令名称。

## 同系列插件

以下均由 **YepStar** 创作和维护，属于「冷月系」，可按需搭配使用。

- [Zlib知识库](https://github.com/YepStar/astrbot_plugin_zlib_knowledge)：找书、整理书架与知识库。
- [网易云音乐](https://github.com/YepStar/astrbot_plugin_netease_browser)：找歌、分享歌曲和一起听。
- [自然意图分析](https://github.com/YepStar/astrbot_plugin_intent_bridge)：把自然语言变成已接入功能的指令调用。

- [第三方平台](https://github.com/YepStar/astrbot_plugin_third_party_platforms)：统一安排 B站、小黑盒等平台的浏览、互动与分享。

[详细使用说明与技术参考](docs/使用与技术参考.md)

## 全局沉浸式记忆（1.3.0）

在插件配置或记忆面板切换“全局沉浸式记忆”，默认开启，统一作用于所有现有及新建分支。旧分支 scene_memory_policy 仅兼容历史数据，不再覆盖插件全局设置。关闭全局开关后所有分支恢复按来源总结口径。

沉浸式记事避免在摘要、事实及关系条目末尾机械追加角色扮演免责声明。每个分支仍保留自己的来源和召回范围；假设、愿望、未完成计划与外部资料仍按其原意保存。既有记忆和回退节点不会因开关变化自动重写。
