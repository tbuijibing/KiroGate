# KiroGate - Docker Image
FROM python:3.11-slim

# 工作目录
WORKDIR /app

# Python 环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖（避免部�?pip 包报错）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY geek_gateway/ ./geek_gateway/
COPY main.py .

# 创建数据目录（在创建用户前，确保挂载时有正确权限�?
RUN mkdir -p /app/data

# 创建�?root 用户
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# 暴露端口（Fly 必须�?
EXPOSE 8000

# ⚠️【重要】调试阶段先不加 HEALTHCHECK
# 等服务稳定后再加�?/health

# 启动 FastAPI
CMD ["python", "main.py"]
