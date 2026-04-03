#!/bin/bash

aeli_python_path="python"
while getopts p:c: flag
do
    case "${flag}" in
        p) aeli_python_path=${OPTARG};;
        c) custom_code_path=${OPTARG};;
    esac
done

# Install dependencies
$aeli_python_path -m pip install -r requirements.txt --no-cache-dir

# Apply database migrations
echo "Apply database migrations for aistudiobot"
$aeli_python_path manage.pyc migrate aistudiobot

# Deploy custom code
echo "Deploying custom code"
if ! [ -z "$custom_code_path" ]
then
    $aeli_python_path copy_custom.pyc -p $aeli_python_path -c $custom_code_path
else
    $aeli_python_path copy_custom.pyc -p $aeli_python_path
fi

status=$?
if ! [ $status -eq 0 ]
then
    echo "Error while deploying custom chatbot webservice code"
    exit $status
fi

# Generate dummy data
# python manage.pyc generate_data

# Start server
echo "Starting server"
# daphne -p 3978 -b 0.0.0.0 webchat_channel.routing:application
$aeli_python_path manage.pyc runserver