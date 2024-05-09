# Lockedin AI

Design Visulization
https://link.excalidraw.com/l/22oiqklLKad/AS0OxR4LTdy#

1. Then fill in your API key in the config.yaml
Create a config.yaml under backend folder and fill in all the keys
```
config.yaml
```

2. Install Poetry for package managing and install all the dependencies in Mac OS
```
curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py | python -

source `poetry env info --path`/bin/activate

poetry install

poetry run python server.py

poetry shell

poetry install
```
   
4. Create a firebase.json under backend folder for firebase authentication

5. Important env variables
DISABLE_GCLOUD_LOGGING(You don't need to use Google logging unless you use GCP to check log errors and debug)
ENVIRONMENT="local"

7. Initialize your gcloud and choose project: lockedinai-6fb81
```
gcloud init
```
After you initialized the google cloud
  
```
gcloud auth application-default login
```

6. Run the server(make sure you're in backend folder)
```
python interviewai/server.py
```
  or if you don't want to set the environment variable. This file contains the environment variable [export ENVIRONMENT="local"]
```
 ./main.ps1  
```
## PR Review
```
git checkout -b your_new_branch
git add -A
git commit -m 'some message'
git push origin your_new_branch
```
Then submit a PR in github UI


# Deployment

Get the latest dependencies since we are using poetry: 

```commandline
poetry export -f requirements.txt --output requirements.txt --without-hashes   
```

Build the docker and push it gcp artifact registry: 
```
PROJECT_ID="lockedinai-6fb81" ./deploy-gcp.sh
```




# (Archived) Below are for deployment, for local testing please ignore below 
## Docker
```
# Build the Docker image
docker build -t interviewai-image .

# Run the Docker container
docker run -p 5001:5001 interviewai-image
```

## Audio Setup
### Install VB-Cable
https://vb-audio.com/Cable/

find your device index
```
python interviewai/speech/check_device.py
```

example output
```
(base) ➜  interviewai git:(master) ✗ python interviewai/speech/check_device.py
{'index': 0, 'structVersion': 2, 'name': 'U28E850', 'hostApi': 0, 'maxInputChannels': 0, 'maxOutputChannels': 2, 'defaultLowInputLatency': 0.01, 'defaultLowOutputLatency': 0.009833333333333333, 'defaultHighInputLatency': 0.1, 'defaultHighOutputLatency': 0.019166666666666665, 'defaultSampleRate': 48000.0}
{'index': 1, 'structVersion': 2, 'name': 'Sceptre F27', 'hostApi': 0, 'maxInputChannels': 0, 'maxOutputChannels': 2, 'defaultLowInputLatency': 0.01, 'defaultLowOutputLatency': 0.009833333333333333, 'defaultHighInputLatency': 0.1, 'defaultHighOutputLatency': 0.019166666666666665, 'defaultSampleRate': 48000.0}
{'index': 2, 'structVersion': 2, 'name': 'Logitech BRIO', 'hostApi': 0, 'maxInputChannels': 2, 'maxOutputChannels': 0, 'defaultLowInputLatency': 0.004583333333333333, 'defaultLowOutputLatency': 0.01, 'defaultHighInputLatency': 0.013916666666666667, 'defaultHighOutputLatency': 0.1, 'defaultSampleRate': 48000.0}
{'index': 3, 'structVersion': 2, 'name': 'MacBook Pro Microphone', 'hostApi': 0, 'maxInputChannels': 1, 'maxOutputChannels': 0, 'defaultLowInputLatency': 0.05229166666666667, 'defaultLowOutputLatency': 0.01, 'defaultHighInputLatency': 0.05695833333333333, 'defaultHighOutputLatency': 0.1, 'defaultSampleRate': 96000.0}
{'index': 4, 'structVersion': 2, 'name': 'MacBook Pro Speakers', 'hostApi': 0, 'maxInputChannels': 0, 'maxOutputChannels': 2, 'defaultLowInputLatency': 0.01, 'defaultLowOutputLatency': 0.018708333333333334, 'defaultHighInputLatency': 0.1, 'defaultHighOutputLatency': 0.028041666666666666, 'defaultSampleRate': 48000.0}
{'index': 5, 'structVersion': 2, 'name': 'VB-Cable', 'hostApi': 0, 'maxInputChannels': 2, 'maxOutputChannels': 2, 'defaultLowInputLatency': 0.01, 'defaultLowOutputLatency': 0.0013333333333333333, 'defaultHighInputLatency': 0.1, 'defaultHighOutputLatency': 0.010666666666666666, 'defaultSampleRate': 48000.0}
{'index': 6, 'structVersion': 2, 'name': 'ZoomAudioDevice', 'hostApi': 0, 'maxInputChannels': 2, 'maxOutputChannels': 2, 'defaultLowInputLatency': 0.01, 'defaultLowOutputLatency': 0.03333333333333333, 'defaultHighInputLatency': 0.1, 'defaultHighOutputLatency': 0.042666666666666665, 'defaultSampleRate': 48000.0}
{'index': 7, 'structVersion': 2, 'name': 'Multi-Output Device', 'hostApi': 0, 'maxInputChannels': 0, 'maxOutputChannels': 2, 'defaultLowInputLatency': 0.01, 'defaultLowOutputLatency': 0.0013333333333333333, 'defaultHighInputLatency': 0.1, 'defaultHighOutputLatency': 0.010666666666666666, 'defaultSampleRate': 48000.0}
```

update config.yml
make sure your device has maxInputChannels >= 1 (thus has input)
Example:
```
SPEAKER_DEVICE_INDEX: 5 # VB-Cable
MIC_DEVICE_INDEX: 3  # MacBook Pro Microphone
```

## ECS Copilot deploy

```
copilot init --app lockedinai \
  --name api \
  --type 'Load Balanced Web Service' \
  --image '507254053937.dkr.ecr.us-west-1.amazonaws.com/lockedinai' \
  --tag 'latest' \
  --port 5001 \
  --deploy
```


## GCP cloud run deploy
```
gcloud run deploy lockedinai --allow-unauthenticated --source=. --update-env-vars=AWS_ACCESS_KEY_ID=xxxx,AWS_SECRET_ACCESS_KEY=xxxx --port=5001
```

## Python package deploy
https://cloud.google.com/artifact-registry/docs/python/store-python

```
gcloud artifacts repositories create interviewai \
    --repository-format=python \
    --location=us-central1 \
    --description="LockedIn AI python package"
```

Then trigger via cloud build in cloudbuild.yaml

You will need to bump up version in setup.py to make it happen.

### Cloud function use private package
https://cloud.google.com/functions/docs/writing/specifying-dependencies-python#using_private_dependencies
e.g. in requirements.txt
```
--extra-index-url https://us-central1-python.pkg.dev/lockedinai-6fb81/interviewai/
interviewai
google-cloud-storage
```


## Stripe
https://dashboard.stripe.com/test/webhooks/create?endpoint_location=local

```
stripe login
stripe listen --forward-to localhost:5001/webhook
stripe trigger payment_intent.succeeded
```

Active Subs
https://stripe.com/docs/billing/subscriptions/webhooks#active-subscriptions

Look for `invoice.paid`
