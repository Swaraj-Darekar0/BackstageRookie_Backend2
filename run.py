from app import create_app
import os

app = create_app()

if __name__ == '__main__':
    # debug=True enables auto-reloading and is for development only.
    # Do not use debug=True in a production environment.
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
