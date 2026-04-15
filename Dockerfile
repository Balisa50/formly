FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Force cache invalidation for source code
RUN echo "build-v11-groq-fallback-chain-react-select-label"
COPY . .

RUN mkdir -p data/uploads

EXPOSE 10000

CMD ["uvicorn", "formly.api:app", "--host", "0.0.0.0", "--port", "10000"]
