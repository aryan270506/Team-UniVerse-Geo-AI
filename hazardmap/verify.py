"""CLIP crop verifier for open-vocabulary detections.

Open-vocab detectors hallucinate on generic prompts (an ordinary road becomes a
"flooded road"). Each candidate box is cropped and scored by CLIP against the
hazard description *and* a set of ordinary street-scene negatives; it survives only
if the hazard wins.
"""
import clip
import cv2
import numpy as np
import torch
from PIL import Image

POSITIVE = {
    "flooding": ["a photo of a flooded road covered in water", "a photo of a large puddle of water on a street"],
    "fallen_tree": ["a photo of a fallen tree lying across a road", "a photo of broken tree branches lying on a road"],
    "power_line": ["a photo of a fallen power line lying on the ground", "a photo of a broken electric pole"],
    "debris": ["a photo of debris and rubble scattered on a road", "a photo of storm wreckage and broken material"],
    "landslide": ["a photo of a landslide with mud and rocks on a road"],
    "blockage": ["a photo of a traffic cone", "a photo of a road barricade"],
}
NEGATIVE = [
    "a photo of a normal dry asphalt road", "a photo of road lane markings", "a photo of a car",
    "a photo of the sky", "a photo of a building", "a photo of a car hood through a windshield",
    "a photo of a road sign", "a photo of trees beside a road", "a photo of a standing utility pole with wires",
    "a photo of grass", "a photo of a sidewalk",
]


class ClipVerifier:
    def __init__(self, device: str = "mps", threshold: float = 0.45):
        self.device = device
        self.threshold = threshold
        self.model, self.pre = clip.load("ViT-B/32", device=device)
        self.cats = list(POSITIVE)
        texts = [t for c in self.cats for t in POSITIVE[c]] + NEGATIVE
        self.owner = [c for c in self.cats for _ in POSITIVE[c]] + [None] * len(NEGATIVE)
        with torch.no_grad():
            tf = self.model.encode_text(clip.tokenize(texts).to(device)).float()
        self.text = tf / tf.norm(dim=-1, keepdim=True)

    def scores(self, frame: np.ndarray, boxes: list, pad: float = 0.15) -> list[dict]:
        """Per box: probability mass assigned to each hazard category (negatives absorb the rest)."""
        if not boxes:
            return []
        h, w = frame.shape[:2]
        crops = []
        for x1, y1, x2, y2 in boxes:
            px, py = (x2 - x1) * pad, (y2 - y1) * pad
            c = frame[int(max(0, y1 - py)):int(min(h, y2 + py)), int(max(0, x1 - px)):int(min(w, x2 + px))]
            crops.append(self.pre(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))))
        with torch.no_grad():
            imf = self.model.encode_image(torch.stack(crops).to(self.device)).float()
            imf = imf / imf.norm(dim=-1, keepdim=True)
            probs = (100 * imf @ self.text.T).softmax(-1).cpu().numpy()
        out = []
        for p in probs:
            agg = {c: 0.0 for c in self.cats}
            for o, v in zip(self.owner, p):
                if o:
                    agg[o] += float(v)
            out.append(agg)
        return out

    def accept(self, frame: np.ndarray, boxes: list, cats: list[str]) -> list[float]:
        """Verified score for each (box, category); 0.0 means rejected."""
        res = []
        for s, cat in zip(self.scores(frame, boxes), cats):
            v = s.get(cat, 0.0)
            res.append(v if v >= self.threshold else 0.0)
        return res
