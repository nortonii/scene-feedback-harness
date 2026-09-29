# 更新记录

- 每次实际更新（功能、修复、接口、配置或文档）都必须在 `CHANGELOG.md` 新增一条记录，并与该次改动一起提交。
- 按日期和时间倒序排列；使用 UTC+08:00。日期标题为 `## YYYY-MM-DD`，条目标题为 `### HH:MM · 更新标题`。
- 用中文说明具体改动、用户可见的效果，以及影响使用的限制或兼容行为。需要验证的变更写明实际验证结果，不把计划、取消或尚未完成的工作写成已交付。
- 一次提交包含同一更新的多个文件时使用一个条目；多个独立提交分别记录。保留旧条目，不通过改写旧记录代替本次记录。
- 历史提交可以附已知的 GitHub 提交链接；本次提交的哈希尚未生成，不要为添加自引用再创建一个提交。
- 提交前运行 `python3 scripts/check_changelog.py --staged`。当前工作区已启用 `.githooks/pre-commit`；新克隆可运行 `git config core.hooksPath .githooks` 启用相同检查。GitHub Actions 也会检查新增提交。

# 项目维护

- [CHANGELOG.md](CHANGELOG.md) 是更新记录的唯一主文件；五语 README 只链接它。
- 维护方法和检查命令见 [CONTRIBUTING.md](CONTRIBUTING.md)。
