# Test cases

Run all (about 20 s):  `python3 -m unittest discover -s tests -t .`
Run one file:           `python3 -m unittest tests.test_engine -v`

| Area | Test | What it proves |
|---|---|---|
| FCFS | `test_fcfs_runs_in_arrival_order` | arrival order wins, priority ignored |
| Priority | `test_priority_runs_highest_priority_first` | 1 before 5 before 9 |
| Priority | `test_priority_tie_broken_by_arrival`, `test_aging_prevents_starvation` | tie-break and aging |
| Round Robin | `test_round_robin_preempts_and_all_jobs_finish` | preemptions >= 1, intervals overlap, wall time > active time |
| Parallelism | `test_parallel_workers` | `--workers 2` runs two jobs together |
| Dependencies | `test_dependent_waits_for_prerequisite` | child starts after prerequisite ended |
| Dependencies | `test_failed_prerequisite_blocks_chain` | failure cascades, runs marked `skipped`, `failures.log` written |
| Dependencies | `test_cycle_detection`, CLI `test_dependency_rules` | cycles rejected, cannot delete a needed job |
| Retries | `test_retry_until_success` | failed, failed, success on attempt 3 |
| Retries | `test_retries_exhausted_marks_failed` | final state `failed`, exit code and output stored |
| Timeout | `test_timeout_kills_job` / `test_timeout_escalates_to_sigkill` | exit -15 (SIGTERM) / -9 (SIGKILL when SIGTERM is ignored) |
| Failure | `test_missing_command_reports_127` | shell "command not found" |
| Logging | `test_logging_fields` | start, end, duration, exit code, stdout+stderr captured |
| Time-based | `test_periodic_job_runs_repeatedly`, `test_future_one_time_job_waits` | periodic repeats; one-time waits for its time |
| Time-based | `CronTests` (8 tests) | */15, daily, weekdays, lists/ranges, @aliases, dom/dow OR, invalid input |
| Control | `test_pending_paused_job_is_not_run` | paused jobs are never scheduled |
| Control | `test_pause_and_resume_running_job_with_signals` | process really is in state `T` (stopped) then continues |
| Control | `test_kill_running_job` | run recorded as `killed` |
| Robustness | `test_second_engine_is_refused`, `test_recovery_of_orphaned_running_job` | file lock, crash recovery |
| Validation | `ValidationTests`, CLI `test_validation_errors`, `test_duplicate_name_rejected` | bad names, durations, priorities, cron, missing scripts |
| CLI | `test_add_list_show`, `test_pause_resume_edit_delete`, `test_execute_and_start_end_to_end`, `test_scheduling_options`, `test_load_demo_jobs` | the whole command set |
