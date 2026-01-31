"""
生成占位图标的脚本
"""
from PIL import Image, ImageDraw, ImageFont

def create_icon(size, filename):
    # 创建紫色背景的图标
    img = Image.new('RGB', (size, size), color='#6366f1')
    draw = ImageDraw.Draw(img)
    
    # 绘制字母 P
    try:
        font = ImageFont.truetype("arial.ttf", int(size * 0.6))
    except:
        font = ImageFont.load_default()
    
    text = "P"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    position = ((size - text_width) // 2, (size - text_height) // 2 - int(size * 0.1))
    draw.text(position, text, fill='white', font=font)
    
    img.save(filename)
    print(f"Created {filename}")

if __name__ == '__main__':
    import os
    os.makedirs('public/icons', exist_ok=True)
    
    create_icon(16, 'public/icons/icon16.png')
    create_icon(48, 'public/icons/icon48.png')
    create_icon(128, 'public/icons/icon128.png')
    
    print("All icons created successfully!")
