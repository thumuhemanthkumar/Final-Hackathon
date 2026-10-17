#!/bin/bash
# Guided demonstration of every major feature.  Takes about 1 minute.
# Usage:  ./demo.sh            (uses a throw-away data directory, /tmp/jobsched_demo)
cd "$(dirname "$0")" || exit 1
export JOBSCHED_HOME=${JOBSCHED_HOME:-/tmp/jobsched_demo}
rm -rf "$JOBSCHED_HOME"
J="python3 jobsched.py"
S="$PWD/examples/scripts"

step() { printf '\n\n########## %s ##########\n' "$1"; }
run()  { printf '\n$'; for a in "$@"; do case "$a" in *[!A-Za-z0-9_./:=@%+,*-]*) printf " '%s'" "$a";; *) printf ' %s' "$a";; esac; done; printf '\n'; "$@"; }

step "1. JOB CREATION + INPUT VALIDATION"
run $J add hello --cmd "echo Hello from the scheduler" --desc "first job" --priority 5
run $J add hello --cmd "echo duplicate"           || true
run $J add "bad name" --cmd "echo x"              || true
run $J add broken --cmd "echo x" --cron "61 * * * *" || true
run $J list

step "2. FCFS SCHEDULING  (jobs queued A, B, C -> run in that order)"
run $J add job-A --cmd "echo A; sleep 1" --priority 5
run $J add job-B --cmd "echo B; sleep 1" --priority 1
run $J add job-C --cmd "echo C; sleep 1" --priority 3
for j in job-A job-B job-C; do $J execute $j > /dev/null; done
run $J start --algorithm fcfs --until-idle --tick 0.1

step "3. PRIORITY SCHEDULING  (same jobs: B(1) -> C(3) -> A(5))"
for j in job-A job-B job-C; do $J execute $j > /dev/null; done
run $J start --algorithm priority --until-idle --tick 0.1

step "4. ROUND ROBIN  (two CPU-bound jobs, 1s quantum: SIGSTOP / SIGCONT time slicing)"
run $J add rr-X --cmd "python3 $S/cpu_burn.py 2 X"
run $J add rr-Y --cmd "python3 $S/cpu_burn.py 2 Y"
for j in rr-X rr-Y; do $J execute $j > /dev/null; done
run $J start --algorithm rr --quantum 1 --until-idle --tick 0.1

step "5. DEPENDENCIES  (queued deploy, test, build -> runs build, test, deploy)"
run $J add build  --cmd "echo compiling...; sleep 1"
run $J add test   --cmd "echo running tests; sleep 1" --depends-on build
run $J add deploy --cmd "echo deploying"               --depends-on test
for j in deploy test build; do $J execute $j > /dev/null; done
run $J start --until-idle --tick 0.1

step "6. FAILURE CASCADE  (prerequisite fails -> dependents are skipped)"
run $J add fetch-data --cmd "echo downloading; exit 7"
run $J add process-data --cmd "echo processing" --depends-on fetch-data
run $J add publish-data --cmd "echo publishing" --depends-on process-data
for j in publish-data process-data fetch-data; do $J execute $j > /dev/null; done
run $J start --until-idle --tick 0.1

step "7. RETRIES  (fails twice, succeeds on the 3rd attempt)"
rm -f /tmp/jobsched_demo_flaky.count
run $J add flaky --cmd "sh $S/flaky.sh 3 /tmp/jobsched_demo_flaky.count" --retries 3 --retry-delay 1
run $J execute flaky
run $J start --until-idle --tick 0.1

step "8. EXECUTION TIMEOUT  (sleep 30 with a 2 second limit)"
run $J add too-slow --cmd "sleep 30" --timeout 2
run $J execute too-slow
run $J start --until-idle --tick 0.1

step "9. TIME-BASED AUTOMATION  (one-time --in 2s, periodic --every 3s, cron)"
run $J add one-shot --cmd "echo one-time job fired" --in 2s
run $J add heartbeat --cmd "echo beat" --every 3s
run $J add nightly --cmd "echo nightly" --cron "0 2 * * *"
run $J add weekdays --cmd "echo standup" --cron "30 9 * * mon-fri"
run $J start --duration 8 --tick 0.2

step "10. PAUSE / RESUME / KILL OF A RUNNING JOB  (SIGSTOP / SIGCONT / SIGTERM)"
run $J add long-task --cmd "for i in 1 2 3 4 5 6 7 8; do echo step \$i; sleep 1; done"
run $J execute long-task
$J start --duration 9 --tick 0.2 > "$JOBSCHED_HOME/bg.out" 2>&1 &
ENGINE=$!
sleep 2.5
run $J pause long-task
sleep 2
run $J status
run $J resume long-task
sleep 1.5
run $J kill long-task
wait $ENGINE
printf '\n--- scheduler output ---\n'; cat "$JOBSCHED_HOME/bg.out"

step "11. EDIT, DELETE, SAMPLE AUTOMATION JOBS"
run $J edit hello --priority 2 --desc "edited description" --retries 1
run $J show hello
run $J delete hello
run $J load-demo
for j in backup-docs disk-usage-check write-timestamp; do $J execute $j > /dev/null; done
run $J start --until-idle --tick 0.1
printf '\n$ cat %s/timestamps.log\n' "$JOBSCHED_HOME"; cat "$JOBSCHED_HOME/timestamps.log"
printf '\n$ ls %s/backups\n' "$JOBSCHED_HOME"; ls "$JOBSCHED_HOME/backups"

step "12. MONITORING, HISTORY, LOGS, FAILURE REPORT"
run $J status
run $J history --limit 12
run $J history --status success --limit 3
run $J output 3
run $J failures --limit 6
printf '\n$ cat %s/failures.log   (written by the scheduler)\n' "$JOBSCHED_HOME"; cat "$JOBSCHED_HOME/failures.log"
echo; echo "Demo finished.  Data directory: $JOBSCHED_HOME"
