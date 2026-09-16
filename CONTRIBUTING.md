# 贡献指南

感谢你对伏羲 RAG 知识库系统的关注！

## 如何贡献

### 报告 Bug

1. 在 GitHub Issues 中创建新 Issue
2. 描述问题的复现步骤
3. 附上错误日志和环境信息

### 提交代码

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交改动：`git commit -m "feat: 描述你的改动"`
4. 推送分支：`git push origin feature/your-feature`
5. 创建 Pull Request

### 代码规范

- Python：遵循 PEP 8
- Vue：遵循 Vue 3 Composition API 规范
- 提交信息：使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式
  - `feat:` 新功能
  - `fix:` 修复 Bug
  - `docs:` 文档更新
  - `refactor:` 重构
  - `test:` 测试相关
  - `chore:` 构建/工具相关

### 开发环境

```bash
# 克隆
git clone https://github.com/1PGAT6466/-RAG-.git
cd -RAG-

# 后端
pip install -r requirements.txt
cp .env.example .env  # 编辑配置
python server.py

# 前端
cd frontend
npm install
npm run dev
```

### 测试

```bash
# 冒烟测试
python scripts/smoke_test.py

# 检索评测
python scripts/retrieval_benchmark.py
```

## License

MIT License
