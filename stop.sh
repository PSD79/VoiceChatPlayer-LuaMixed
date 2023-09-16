DIR_NAME=$(basename "$PWD")
tmux kill-session -t $DIR_NAME-$1
