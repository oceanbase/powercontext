import React from 'react';
import './Slider.css';

/**
 * Slider 组件属性
 */
interface SliderProps {
  /** 标签文本 */
  label?: string;
  
  /** 当前值 */
  value: number;
  
  /** 最小值 */
  min: number;
  
  /** 最大值 */
  max: number;
  
  /** 步长 */
  step?: number;
  
  /** 变化回调 */
  onChange: (value: number) => void;
  
  /** 值格式化函数 */
  formatValue?: (value: number) => string;
  
  /** 帮助文本 */
  helpText?: string;
}

/**
 * 滑块组件
 * 数值范围选择器
 */
export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  formatValue,
  helpText
}: SliderProps): JSX.Element {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(parseFloat(e.target.value));
  };

  const displayValue = formatValue ? formatValue(value) : value.toString();

  return (
    <div className="slider-container">
      {label && (
        <div className="slider-header">
          <label className="slider-label">{label}</label>
          <span className="slider-value">{displayValue}</span>
        </div>
      )}
      
      <input
        type="range"
        className="slider-input"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={handleChange}
      />
      
      <div className="slider-range">
        <span>{min}</span>
        <span>{max}</span>
      </div>
      
      {helpText && <div className="slider-help-text">{helpText}</div>}
    </div>
  );
}
