export ENVIRONMENT="dev"
export AWS_ACCESS_KEY_ID=""
export AWS_SECRET_ACCESS_KEY=""
# force sync requirements.txt
poetry export -f requirements.txt --output requirements.txt --without-hashes
gunicorn -b 0.0.0.0:5001 --threads 100 -w 1 application:application
#python application.py