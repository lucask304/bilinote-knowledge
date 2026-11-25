# BiliNote 知识笔记版
基于 [BiliNote](https://github.com/JefferyHcool/BiliNote) v2.4.5 的 Windows 定制前端与启动器，用于长视频复习。启动时连接或静默启动原版后台，再打开知识笔记窗口。

## 体验
先安装并配置原版 Windows BiliNote v2.4.5（已有转写和模型配置可直接使用），安装 Python 3.10+（含 Tk）。下载此仓库 ZIP 并解压到可写目录，双击“启动知识笔记版.pyw”。首次选择包含 BiliNoteBackend.exe 的原版安装目录。安装 Edge 时以独立窗口展示，否则使用默认浏览器。

输入视频链接或选择本地视频生成笔记。按话题整理时间范围、知识点、难点脚注和逻辑关系，支持自动保存 Markdown、Word 及可选的 Obsidian 同步。文档默认在用户目录 Documents/BiliNote。模型调用使用本机配置，可能产生费用。关闭界面后后台继续处理任务。

仓库包含定制服务、预构建界面、提示词和文档导出代码。原版项目采用 MIT 许可，见 LICENSE。
