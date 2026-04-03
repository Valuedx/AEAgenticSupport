#!/bin/bash

# Collect static files
#echo "Collect static files"
#python manage.py collectstatic --noinput

echo "Applying permissions to CBWS home"
sudo /bin/chown -R aistudio:aistudio_group $CHATBOT_WEB_SERVICE_HOME
sudo /usr/sbin/deluser aistudio sudo

# Apply database migrations
echo "Apply database migrations for aistudiobot"
python manage.py migrate -y

if ! [ -z "$CUSTOM_CODE_PATH" ]
then
    # Copy custom code
    echo "Custom Path : $CUSTOM_CODE_PATH"
    echo "Deploying custom code"
    python copy_custom.py -c $CUSTOM_CODE_PATH
    status=$?
    if ! [ $status -eq 0 ]
    then
        echo "Error while copying custom chatbot webservice code"
        exit $status
    fi
else
    echo "Custom code path not provided"
fi

# Generate dummy data
# python manage.py generate_data

# Commenting the code until AELIS-1701 is fixed 
# ---- BEGIN ----
# If whatsapp channel is present, sign public key
# if [[ "$CBWS_CHANNELS" == *"ae_whatsapp"* ]]; then
#     python manage.py register_public_key
# fi
# ---- END ----

# Start server
echo "Starting server"
daphne -p 3978 -b 0.0.0.0 webchat_channel.routing:application
