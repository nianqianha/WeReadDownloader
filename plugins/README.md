# plugins/

- `weread-importer.koplugin/`：给 KOReader 用的独立导入器，**开发中、未随安装包发布**，
  主程序也不引用它。2026-10 之前它被误放在 `release-fresh/`（PyInstaller 构建输出目录）里，
  构建清理时容易丢，已移到此处。
