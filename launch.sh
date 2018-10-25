#!/usr/bin/env bash

export FLASK_APP=src/index.py

PORT=3000
HOST=127.0.0.1

flask run -h $HOST -p $PORT

