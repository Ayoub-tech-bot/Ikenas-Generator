FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the port Hugging Face Spaces uses
EXPOSE 7860
ENV PORT=7860

# Command to run the application
CMD ["python", "build/server.py"]
