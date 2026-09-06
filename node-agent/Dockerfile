FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# python main.py (not `uvicorn main:app ...`) so mTLS settings come from
# .env instead of duplicated CLI flags — see main.py's __main__ block.
CMD ["python", "main.py"]
