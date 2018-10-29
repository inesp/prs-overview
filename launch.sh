#!/usr/bin/env bash

export FLASK_APP=src/index.py
export FLASK_ENV=development

PORT=3000
HOST=127.0.0.1

flask run -h $HOST -p $PORT

