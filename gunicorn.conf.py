bind = "0.0.0.0:5050"
workers = 2
threads = 4
worker_class = "gthread"
timeout = 120
keepalive = 5
accesslog = "logs/access.log"
errorlog = "logs/error.log"
loglevel = "info"
# Capture worker stdout/stderr into the errorlog above. Without this,
# `print()` calls from application code (e.g. `[newsalerts] Filtered
# N low-quality alerts`, `[dashboard] Model A rescored ...`, ML
# import-time diagnostics) bypass `errorlog` and land in the systemd
# journal — or `/dev/null` if the unit doesn't have `StandardOutput=`
# set. Routing them through gunicorn keeps every signal we care about
# in one tail-able file alongside gunicorn's own master/worker
# lifecycle lines.
capture_output = True
