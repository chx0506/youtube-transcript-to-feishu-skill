# youtube-transcript-to-feishu

Cursor Agent Skill：把 YouTube 视频或网页文章整理成**一句中文 + 一句英文**的双语飞书文档，并追加到「优质信息」索引页。

适用场景：

- YouTube 链接 → 双语逐字稿飞书文档
- 网页文章链接 → 双语翻译稿飞书文档
- 自动登记到「优质信息」wiki 列表

## 文件结构

```text
SKILL.md                      # Skill 主说明（Agent 执行协议）
scripts/fetch_transcript.py   # YouTube 字幕抓取脚本（stdlib + 网络）
```

## 使用方式

在 Cursor 中安装/引用本 Skill 后，直接发送 YouTube 或文章链接，并说明需要逐字稿 / 翻译稿 / 整理成飞书文档即可。

依赖：

- Python 3（抓取字幕）
- 已配置可用的 `lark-doc` Skill（飞书文档读写）

## License

MIT
