# Use an official Python runtime as a parent image
FROM python:3.10-slim 
# Choose a Python version your bot uses, e.g., 3.9, 3.10, 3.11. Slim is smaller.

# Set the working directory in the container
WORKDIR /app

# Install system dependencies, including FFmpeg and Git (if needed for anything)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container at /app
COPY . .

# Command to run your application
# Replace your_main_bot_script.py with the actual name of your main bot file
CMD ["python", "downloadbot.py"]
