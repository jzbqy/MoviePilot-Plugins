MoviePilot官方插件市场：https://github.com/jxxghp/MoviePilot-Plugins
MoviePilot第三方插件市场：https://github.com/jzbqy/MoviePilot-Plugins

## 一键跳校（MoviePilot V2 插件）
- 目录：`plugins.v2/reseedjump/`
- 定义：`package.v2.json`（键名与目录名一致：`reseedjump`）

### 功能
- 仅支持 qBittorrent：导出 `.torrent` → 删除原任务（不删文件）→ 重新添加（跳过校验）
- 保留原保存路径、可保留原分类、添加完成标签

### 安装
1. 将本目录推送至 GitHub 仓库（建议仅保留本插件子目录与 `package.v2.json`）
2. 在 MoviePilot V2 后台 → 插件 → 第三方源，填入你的仓库原始地址
3. 在插件市场中找到“`一键跳校`”并安装

示例源（raw）结构：
```
<repo-root>/
  package.v2.json
  plugins.v2/
    reseedjump/
      __init__.py
      README.md
```

### 配置要点
- 下载器：选择要处理的下载器（支持多选）
- 执行周期：如 `*/10 * * * *`（每 10 分钟）。或勾选“立即运行一次”
- 仅处理暂停任务：建议开启
- 仅处理含这些标签：例如 `IYUU自动辅种`
- 处理完成后打标签：默认“已跳校”
- 种子文件目录映射(JSON)：为每个下载器名称提供 `.torrent` 所在目录，如：
```json
{
  "QB名称": "D:/QB/BT_backup",
}
```

### 注意事项
- 需能从磁盘读取 `.torrent` 文件；若目录配置错误，将跳过并记录日志
- 仅支持 qBittorrent
- 若你仓库含多插件，请确保 `package.v2.json` 的键与目录名一一对应


