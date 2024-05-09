from interviewai.server import create_app, socketio, PORT

application = create_app()

if __name__ == "__main__":
    socketio.run(application, debug=False, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)