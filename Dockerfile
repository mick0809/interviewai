# Base image
FROM python:3.11

# Set working directory
WORKDIR /app

# Copy project files
COPY . /app

# Expose port 5001
EXPOSE 5001


# Install project dependencies
RUN pip install -e .
RUN pip install -r requirements.txt
# prevent API leak
RUN rm -f config.yaml

ENV ENVIRONMENT prod
# Start the server
CMD ["python", "interviewai/server.py"]