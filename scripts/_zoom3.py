from PIL import Image
import glob
for f in sorted(glob.glob("output/_check5/clip*.png")):
    im = Image.open(f)
    w, h = im.size
    crop = im.crop((0, int(h*0.33), w, int(h*0.47)))
    crop = crop.resize((crop.width*2, crop.height*2))
    out = f.replace("clip", "zoom_clip")
    crop.save(out)
