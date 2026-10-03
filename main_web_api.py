from flask import Flask, request, jsonify, send_from_directory
import os
import utils
import config
import database as db
import logging

logger = logging.getLogger(__name__)
app = Flask(__name__, static_folder='web')

@app.route('/', methods=['GET', 'HEAD'])
def index():
    return send_from_directory('web', 'index.html')

@app.route('/api/init', methods=['POST'])
def web_init():
    try:
        init_data = request.json.get('initData')
        user_data = utils.verify_telegram_webapp_data(init_data, config.BOT_TOKEN)
        if not user_data:
            return jsonify({"error": "Unauthorized"}), 401
        
        user_id = user_data.get('id')
        if not user_id:
            return jsonify({"error": "Invalid User Data"}), 400
            
        db.register_or_update_user(user_id, user_data.get('username'), user_data.get('first_name'))
        
        profile = db.get_user(user_id)
        contracts = db.get_user_contracts(user_id)
        # دریافت دسته‌بندی‌ها برای فرم ثبت قرارداد
        categories = db.get_bot_setting("categories", "سایر")
        if isinstance(categories, str): categories = [c.strip() for c in categories.split(',')]
        
        return jsonify({
            "profile": profile,
            "contracts": contracts,
            "categories": categories,
            "stats": {
                "balance": profile.get('wallet_balance', 0),
                "active_contracts": len([c for c in contracts if c.get('status') == 'ACTIVE'])
            }
        })
    except Exception as e:
        logger.error(f"Web Init Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/contracts/create', methods=['POST'])
def create_contract_web():
    try:
        init_data = request.json.get('initData')
        user_data = utils.verify_telegram_webapp_data(init_data, config.BOT_TOKEN)
        if not user_data: return jsonify({"error": "Unauthorized"}), 401
        
        user_id = user_data.get('id')
        data = request.json.get('contractData')
        
        # ثبت قرارداد در دیتابیس با استفاده از متد اصلی پروژه
        contract_id = db.create_contract(
            creator_id=user_id,
            title=data['title'],
            amount=int(data['amount']),
            category=data['category'],
            description=data.get('description', '')
        )
        
        return jsonify({"success": True, "contract_id": contract_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/web/<path:path>')
def send_web_files(path):
    return send_from_directory('web', path)

@app.route('/health', methods=['GET', 'HEAD'])
def health_check():
    return "OK", 200
