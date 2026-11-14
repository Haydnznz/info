`config.json` 只保存系统开关、应用开关和非敏感运行参数。所有外部 API URL、模型 ID、RSS 地址和 API Key 统一放在项目根目录 `.env`，由 `utils/settings.py` 加载。
