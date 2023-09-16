DIR_NAME=$(basename "$PWD")
tmux new-session -d -s $DIR_NAME-$1 "./launch.sh $1"
