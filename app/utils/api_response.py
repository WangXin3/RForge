from flask import jsonify


def success(data=None, message="成功", code=200):
    return jsonify({"code": code, "message": message, "data": data}), code


def error(message="请求失败", code=400, data=None):
    return jsonify({"code": code, "message": message, "data": data}), code
