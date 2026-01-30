import os
from celery import Celery
from flask import Flask, current_app

# This import needs to be relative to the project root for Celery to find it
# In a typical setup, 'app' would be a package directly under where Celery is run from.
# Here, create_app is in backendPA/app/__init__.py
from app import create_app 

def make_celery(app_name=__name__):
    # This URL should be provided by Render's Redis service
    # Fallback to a default if not set, for local development
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    
    celery_app = Celery(
        app_name,
        broker=redis_url,
        backend=redis_url
    )
    
    return celery_app

# Create the Flask application instance
flask_app = create_app()

# Create the Celery application instance
celery = make_celery()

# This is a custom Task class for Celery that ensures a Flask app context
# is available during the execution of a Celery task. This allows tasks
# to use Flask's current_app, current_app.config, etc.
class FlaskCeleryTask(celery.Task):
    def __call__(self, *args, **kwargs):
        with flask_app.app_context():
            return self.run(*args, **kwargs)

celery.Task = FlaskCeleryTask

# Optional: Configuration for Celery - can be done directly or via a config object
# celery.conf.update(flask_app.config) # Update with Flask app config if needed
