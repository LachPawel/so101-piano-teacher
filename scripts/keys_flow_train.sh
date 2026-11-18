#!/bin/zsh
# Train the ears policy on the note-by-note recordings (300 episodes, the arm stays over the keyboard).
#
#   scripts/keys_flow_train.sh
#
# What it does:
#   1. Pushes the merged training set to your Hugging Face account, private.
#   2. Starts one paid cloud job on a100-large and returns at once. The job runs on its own.
#
# It trains from the SmolVLA base model, not from the earlier keyboard policies: those learned the old
# keyboard position and the trip back to the parked pose, and this data has neither.
#
# Cost: 10,000 steps, about 3.5 hours, about $8.50 at $2.50 per hour. The 4 hour limit caps it at $10.
# A checkpoint goes to the Hub every 2,000 steps.
set -e
cd "$(dirname "$0")/.."

# Public on the Hub as PawelLach/so101-piano-flow-300ep; the old name redirects, and the local copy is filed under it.
DATA=${KEYS_FLOW_DATA:-PawelLach/so101_keys_flow_300ep}
DATA_DIR="${HF_LEROBOT_HOME:-$HOME/.cache/huggingface/lerobot}/${DATA}"
STEPS=${KEYS_FLOW_STEPS:-10000}

echo "== 1/2 Pushing ${DATA}"
uv run python -c "
from lerobot.datasets.lerobot_dataset import LeRobotDataset as D
D('${DATA}', root='${DATA_DIR}').push_to_hub(private=True)
print('PUSHED ${DATA}')" 2>&1 | grep -E "PUSHED|Uploading|Committing" || true

echo "== 2/2 Starting the cloud job (${STEPS} steps)"
uv run lerobot-train --policy.path=lerobot/smolvla_base --dataset.repo_id=${DATA} \
  --policy.freeze_vision_encoder=false --policy.train_expert_only=false \
  --dataset.image_transforms.enable=true --dataset.eval_split=0.1 --eval_steps=1000 \
  --rename_map='{"observation.images.front": "observation.images.camera1", "observation.images.ears": "observation.images.camera2"}' \
  --batch_size=64 --steps=${STEPS} --save_freq=2000 \
  --policy.repo_id=PawelLach/smolvla_keys_flow --save_checkpoint_to_hub=true \
  --job.target=a100-large --job.timeout=4h --job.detach=true 2>&1 | grep -E "Job submitted|Job page|Model repo|Cancel|rror"

echo "The job is running in the cloud. You can close the laptop."
