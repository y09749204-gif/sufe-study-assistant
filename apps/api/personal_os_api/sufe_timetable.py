"""Shared SUFE timetable, matching the original project's course-time convention.

Only the bell schedule is shared; no personal courses, teachers or rooms belong here.
"""
_CLOCKS = (
    ('08:00','08:45'), ('08:55','09:40'),
    ('10:05','10:50'), ('11:00','11:45'), ('11:55','12:40'),
    ('13:20','14:05'), ('14:15','15:00'),
    ('15:25','16:10'), ('16:20','17:05'), ('17:15','18:00'),
    ('18:00','18:45'), ('18:55','19:40'), ('19:50','20:35'), ('20:45','21:30'),
)


def sufe_periods():
    return [{'number':i,'start':start,'end':end} for i,(start,end) in enumerate(_CLOCKS,1)]
