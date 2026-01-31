const fs = require('fs');
const path = require('path');

// 创建简单的 PNG 图标 (纯色方块)
function createIcon(size, color, filename) {
  // 这是一个最小的 PNG 文件的 base64 编码 (1x1 紫色像素)
  // 实际开发中应该使用专业的图标设计
  const pngHeader = Buffer.from([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, // PNG signature
  ]);
  
  // 创建简单的纯色 PNG
  // 这只是一个占位符，实际应该使用设计工具创建
  const canvas = require('canvas').createCanvas(size, size);
  const ctx = canvas.getContext('2d');
  
  // 背景色
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, size, size);
  
  // 绘制字母 P
  ctx.fillStyle = '#FFFFFF';
  ctx.font = `bold ${Math.floor(size * 0.6)}px Arial`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('P', size / 2, size / 2);
  
  const buffer = canvas.toBuffer('image/png');
  fs.writeFileSync(filename, buffer);
  console.log(`Created ${filename}`);
}

const iconsDir = path.join(__dirname, 'public', 'icons');
if (!fs.existsSync(iconsDir)) {
  fs.mkdirSync(iconsDir, { recursive: true });
}

try {
  createIcon(16, '#6366f1', path.join(iconsDir, 'icon16.png'));
  createIcon(48, '#6366f1', path.join(iconsDir, 'icon48.png'));
  createIcon(128, '#6366f1', path.join(iconsDir, 'icon128.png'));
  console.log('All icons created successfully!');
} catch (error) {
  console.log('Note: canvas module not installed. You can install it with: npm install canvas');
  console.log('Or create icons manually using design tools.');
  
  // 创建占位符说明文件
  const placeholder = `Icons need to be created manually.
Create three PNG files:
- icon16.png (16x16)
- icon48.png (48x48)  
- icon128.png (128x128)

Use any image editing tool or online generator.`;
  
  fs.writeFileSync(path.join(iconsDir, 'ICONS_NEEDED.txt'), placeholder);
  console.log('Created ICONS_NEEDED.txt as a reminder.');
}
