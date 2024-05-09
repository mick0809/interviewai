#!/bin/bash

set -e

# Define environment variables
PROJECT_ID="${PROJECT_ID}"


# Export dependencies from Poetry to requirements.txt without hashes
poetry export -f requirements.txt --output requirements.txt --without-hashes

# Build the Docker image using the Dockerfile in the current directory
# We'll use a generic name for the image and avoid using a specific tag.
docker build -t us-central1-docker.pkg.dev/"${PROJECT_ID}"/lockedin-docker/lockedin-flask-app .

gcloud auth configure-docker
echo push image to lockedin-flask-app
docker push us-central1-docker.pkg.dev/"${PROJECT_ID}"/lockedin-docker/lockedin-flask-app