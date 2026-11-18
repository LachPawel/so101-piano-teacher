#!/bin/zsh
# Plan B: continue both 5,000-step policies for 5,000 more steps on the bigger dataset (268 episodes).
#
#   scripts/keys_planb.sh
#
# What it does, in order:
#   1. Copies your two 5,000-step policies into their own private model repos on your Hugging Face account,
#      so a cloud job can start from them.
#   2. Pushes the two new training sets (private).
#   3. Starts two paid cloud jobs on a100-large and returns at once. The jobs run on their own.
#
# Cost: about 1.5 h for the ears policy and 1.1 h for camera only, about $6.50 together at $2.50 per hour.
# The time limits below cap it at $8.75. A checkpoint goes to the Hub every 1,000 steps.
set -e
cd "$(dirname "$0")/.."

USER_ID=PawelLach
# Public on the Hub as PawelLach/so101-piano-268ep-camera-ears and -camera-only; the old names redirect, and
# the local copies are filed under them.
DATA=${USER_ID}/so101_keys_teleop_20261116_212951_v2
DATA_DIR="${HF_LEROBOT_HOME:-$HOME/.cache/huggingface/lerobot}/${DATA}"
FRONT='"observation.images.front": "observation.images.camera1"'
EARS='"observation.images.ears": "observation.images.camera2"'

echo "== 1/3 Copying the 5,000-step policies to their own private repos"
for name in both text; do
  uv run python -c "
from huggingface_hub import HfApi
api = HfApi()
repo = '${USER_ID}/smolvla_keys_${name}_5k'
api.create_repo(repo, private=True, exist_ok=True)
api.upload_folder(folder_path='outputs/keys_models/${name}_5k/checkpoints/005000/pretrained_model', repo_id=repo)
print('uploaded', repo)"
done

echo "== 2/3 Pushing the two training sets"
for name in both text; do
  uv run python -c "
from lerobot.datasets.lerobot_dataset import LeRobotDataset as D
D('${DATA}_${name}', root='${DATA_DIR}_${name}').push_to_hub(private=True)
print('pushed ${DATA}_${name}')" 2>&1 | grep -v "torchcodec\|Traceback\|File \|OSError\|dlopen\|Reason\|Referenced\|ctypes\|load_library\|FFmpeg\|exception\|\^\|^ *[0-9]\. \|version\|table\|github\|system\|causes\|for each\|full-shared\|^\s*$" || true
done

echo "== 3/3 Starting the two cloud jobs"
train() {  # name, rename map, time limit
  uv run lerobot-train --policy.path=${USER_ID}/smolvla_keys_$1_5k --dataset.repo_id=${DATA}_$1 \
    --policy.freeze_vision_encoder=false --policy.train_expert_only=false \
    --dataset.image_transforms.enable=true --dataset.eval_split=0.1 --eval_steps=1000 \
    --rename_map="$2" --batch_size=64 --steps=5000 --save_freq=1000 \
    --policy.repo_id=${USER_ID}/smolvla_keys_$1_v2 --save_checkpoint_to_hub=true \
    --job.target=a100-large --job.timeout=$3 --job.detach=true 2>&1 | grep -E "Job submitted|Job page|Model repo|Cancel|rror"
}
train both "{${FRONT}, ${EARS}}" 2h
train text "{${FRONT}}" 90m

echo "Both jobs are running in the cloud. You can close the laptop."
