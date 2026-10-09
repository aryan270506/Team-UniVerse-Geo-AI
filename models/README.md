Place model weights here (not committed, too large):

- `rdd_yolov8s.pt` — RDD2022 YOLOv8s road-damage model:
  `curl -L -o models/rdd_yolov8s.pt https://raw.githubusercontent.com/oracl4/RoadDamageDetection/main/models/YOLOv8_Small_RDD.pt`
- `yolov8s-worldv2.pt` — run once from inside `models/`: `../.venv/bin/python -c "from ultralytics import YOLO; YOLO('yolov8s-worldv2.pt')"`
- CLIP ViT-B/32 downloads automatically on first run.
