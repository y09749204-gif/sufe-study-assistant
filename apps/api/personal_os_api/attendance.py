"""Attendance evidence, distinct from student actions and actual presence."""
import re

VERSION = "attendance-v1"


def scan_attendance(transcript):
    if transcript is None:
        return {"version": VERSION, "status": "no_transcript", "signals": []}
    rows = transcript.segments or [{"text": transcript.text, "start": None}]
    signals = []
    seen = set()
    for row in rows:
        body = row.get("text", "")
        for match in re.finditer(r"[^。！？\n]*[。！？]?", body):
            quote = match[0].replace("添加到笔记", "").strip()
            if not re.search(r"签(?:过|完|了)?到|签退|补签|考勤|点(?:过|完|了)?名|查到课|查出勤", quote):
                continue
            if re.search(r"比如|比方|举个例子|假设", quote):
                kind = "example"
            elif re.search(r"(?:没|未)签到的同学.{0,20}(?:请|需要|要).{0,10}补签", quote):
                kind = "requirement"
            elif re.search(r"但[^。！？]{0,20}(?:请|必须|需要|记得).{0,12}(?:签到|签退|补签)", quote):
                kind = "requirement"
            elif re.search(r"(?:不|没|未|不用|无需).{0,5}(?:签到|签退|考勤|点名)|(?:签到|考勤|点名).{0,6}(?:取消|不用|没有)", quote):
                kind = "negative"
            elif re.search(r"(?:请|大家|你们|同学|需要|必须|记得|别忘|务必).{0,35}(?:签到|签退|补签)|(?:签到|签退|补签).{0,20}(?:截止|之前|完成|二维码|链接|分钟|要求)|(?:从|以后|每次).{0,20}(?:考勤|签到|点名)", quote):
                kind = "requirement"
            elif re.search(r"(?:查|核对|检查|核查|看一下).{0,12}(?:签到|考勤)|点(?:过|完|了)?名|查到课|查出勤", quote):
                kind = "checking"
            elif re.search(r"(?:发|开|开始|进行|现在).{0,10}(?:签到|考勤)|签到.{0,10}(?:发了|开了|开始)", quote):
                kind = "initiated"
            else:
                kind = "mentioned"
            key = (quote, row.get("start"))
            if key in seen:
                continue
            seen.add(key)
            signals.append({"kind": kind, "text": quote, "evidence": body,
                "timestamp_seconds": row.get("start"), "needs_verification": True})
    return {"version": VERSION, "status": "evidence_found" if signals else "not_found", "signals": signals}


def attendance_requirements(transcript):
    result = []
    for signal in scan_attendance(transcript)["signals"]:
        if signal["kind"] != "requirement":
            continue
        quote = signal["text"]
        # Completed events and teachers' own actions are evidence, not new todos.
        if re.search(r"已经|刚才|签过|签完|上次|上节|昨天|我会|我来|有没有|是否", quote):
            continue
        audience = re.search(r"(?:没|未)签到的同学", quote)
        time = re.search(r"(?:下次|下节课|课前|课后|每次|以后|今天|明天|[一二三四五六七八九十\d]+分钟内)", quote)
        result.append({"notice_kind": "classroom", "text": quote, "audience": audience[0] if audience else "待核实", "time_text": time[0] if time else "",
            "evidence": signal["evidence"], "evidences": [{"text": signal["evidence"], "timestamp_seconds": signal["timestamp_seconds"]}],
            "timestamp_seconds": signal["timestamp_seconds"], "needs_verification": True,
            "verification_note": "签到／考勤要求：请核对适用对象、截止时间和是否已在当堂完成；不能据此认定本人已签到。",
            "extraction_method": "literal_rule", "rule": "attendance", "focus": "attendance"})
    return result
