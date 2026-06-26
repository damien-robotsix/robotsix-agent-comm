# vulture whitelist — list symbols that vulture would flag as dead code
# but that are actually used (e.g. re-exports, public API, entry points).
# Entries below are bare expressions — ruff B018 is suppressed per-file
# in pyproject.toml.

broker_url
close
daemon_threads
do_DELETE
do_GET
do_POST
health_check
health_url
list_agents
log_message
on_notification
on_request
receive_message
send_notification
send_request
argv
frame
handle_config_get
handle_config_set
handle_monitor
register_handler
handle_lifecycle
handle_status
args
