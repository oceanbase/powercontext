// 创建最小的有效 PNG 文件
const fs = require('fs');
const path = require('path');

// 16x16 紫色图标的 base64
const icon16 = 'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAAdgAAAHYBTnsmCAAAABlQTFRFYzZvYzZvYzZvYzZvYzZvYzZvYzZvYzZvE9qYCwAAAAd0Uk5TAP//////AAAAAEF4lB8AAAApSURBVDiN7c0xDQAwEMQwuP9OBgSEDEDz0vaTfCFD7cUrIYQQQgghhBBC+A0f2AEDWJp8OgoAAAAASUVORK5CYII=';

// 48x48 紫色图标的 base64
const icon48 = 'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAB2AAAAdgB+lymcgAAABlQTFRFYzZvYzZvYzZvYzZvYzZvYzZvYzZvYzZvE9qYCwAAAAd0Uk5TAP//////AAAAh0lEQVRoge3YsQ2AMAwFQfMThYISJmASRmEiJqFgAgoK3P8VkVKk4F9gydLp09cxqyZJkiRJkiRJkiRJkiRJkiRJ+otaYAM2YAd2YAcOYAcOYAd2YAdOYAfuYAfu4A7u4A6ewA6ewB08gSd4giewgydwB0/gDp7AHdyBO3gCT+AOnsATPIEnkCRJ+osLF4UDwzgeQG4AAAAASUVORK5CYII=';

// 128x128 紫色图标的 base64
const icon128 = 'iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAD6AAAA+gBtXtSawAAABlQTFRFYzZvYzZvYzZvYzZvYzZvYzZvYzZvYzZvE9qYCwAAAAd0Uk5TAP//////AAAA60lEQVR4nO3bMQ0AAAgDMe6fOgIjVPQ9OyGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIb3gAa0oAYAjBHQhAAAAAElFTkSuQmCC';

const iconsDir = path.join(__dirname, 'public', 'icons');

// 写入图标文件
fs.writeFileSync(path.join(iconsDir, 'icon16.png'), Buffer.from(icon16, 'base64'));
fs.writeFileSync(path.join(iconsDir, 'icon48.png'), Buffer.from(icon48, 'base64'));
fs.writeFileSync(path.join(iconsDir, 'icon128.png'), Buffer.from(icon128, 'base64'));

console.log('✓ Created icon16.png');
console.log('✓ Created icon48.png');
console.log('✓ Created icon128.png');
console.log('\nAll placeholder icons created successfully!');
