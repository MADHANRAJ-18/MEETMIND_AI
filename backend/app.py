

import os
from flask import Flask, jsonify
from flask_cors import CORS

from config.settings import settings
from routes.auth_routes import auth_bp
from routes.calendar_routes import calendar_bp
from routes.ai_routes import ai_bp
from routes.meeting_routes import meeting_bp
from routes.timetable_routes import timetable_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    CORS(
        app,
        resources={r"/api/*": {"origins": settings.FRONTEND_URL}},
        supports_credentials=True,
    )

    app.register_blueprint(auth_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(meeting_bp)
    app.register_blueprint(timetable_bp)

    @app.route("/")
    def index():
        return jsonify({"service": "MeetMind AI Backend", "status": "running"})

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "cwd": os.getcwd(), "proxy_cleanup_active": "127.0.0.1:9" not in os.environ.get("HTTP_PROXY", "")})

    @app.errorhandler(400)
    def bad_request(err):
        return jsonify({"error": str(err) if str(err) else "Bad request"}), 400

    @app.errorhandler(404)
    def not_found(_err):
        return jsonify({"error": "Resource not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_err):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def server_error(_err):
        return jsonify({"error": "Internal server error"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.PORT, debug=settings.FLASK_ENV == "development")

