/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

"use strict";

const dateKeyPattern = /^\d{4}-\d{2}-\d{2}$/;

export function resolvePeriodSelection(mode, timezone, customRange = {}, now = new Date()) {
  const today = dateKeyInTimeZone(now, timezone);
  let startDate;
  let endDate;
  if (mode === "week") {
    startDate = addDays(today, -isoWeekdayOffset(today));
    endDate = addDays(startDate, 6);
  } else if (mode === "month") {
    startDate = `${today.slice(0, 7)}-01`;
    endDate = addDays(addMonths(startDate, 1), -1);
  } else if (mode === "custom") {
    startDate = customRange.startDate;
    endDate = customRange.endDate;
    validateDateRange(startDate, endDate);
  } else {
    startDate = today;
    endDate = today;
  }
  return {
    mode,
    startDate,
    endDate,
    period: {
      start: zonedStartOfDay(startDate, timezone),
      end: zonedStartOfDay(addDays(endDate, 1), timezone),
      timezone,
      compare_to_previous_period: true
    }
  };
}

export function validateDateRange(startDate, endDate) {
  if (!isValidDateKey(startDate) || !isValidDateKey(endDate)) {
    throw new Error("periodDatesRequired");
  }
  if (startDate > endDate) {
    throw new Error("periodInvalidRange");
  }
}

export function formatDateRange(startDate, endDate, locale) {
  const formatter = new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC"
  });
  const start = formatter.format(dateKeyToDate(startDate));
  const end = formatter.format(dateKeyToDate(endDate));
  return startDate === endDate ? start : `${start} - ${end}`;
}

function dateKeyInTimeZone(value, timezone) {
  const parts = partsInTimeZone(value, timezone);
  return formatDateKey(parts.year, parts.month, parts.day);
}

function zonedStartOfDay(dateKey, timezone) {
  const {year, month, day} = parseDateKey(dateKey);
  const target = Date.UTC(year, month - 1, day);
  let candidate = target;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const parts = partsInTimeZone(new Date(candidate), timezone, true);
    const represented = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
    const adjustment = target - represented;
    candidate += adjustment;
    if (adjustment === 0) {
      return new Date(candidate).toISOString();
    }
  }
  return new Date(candidate).toISOString();
}

function partsInTimeZone(value, timezone, includeTime = false) {
  const options = {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: timezone
  };
  if (includeTime) {
    options.hour = "2-digit";
    options.minute = "2-digit";
    options.second = "2-digit";
    options.hourCycle = "h23";
  }
  const parts = new Intl.DateTimeFormat("en-CA", options).formatToParts(value);
  const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour || 0),
    minute: Number(values.minute || 0),
    second: Number(values.second || 0)
  };
}

function isoWeekdayOffset(dateKey) {
  const weekday = dateKeyToDate(dateKey).getUTCDay();
  return (weekday + 6) % 7;
}

function addDays(dateKey, days) {
  const date = dateKeyToDate(dateKey);
  date.setUTCDate(date.getUTCDate() + days);
  return formatDateKey(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate());
}

function addMonths(dateKey, months) {
  const {year, month} = parseDateKey(dateKey);
  const date = new Date(Date.UTC(year, month - 1 + months, 1));
  return formatDateKey(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
}

function dateKeyToDate(dateKey) {
  const {year, month, day} = parseDateKey(dateKey);
  return new Date(Date.UTC(year, month - 1, day));
}

function parseDateKey(dateKey) {
  if (!isValidDateKey(dateKey)) {
    throw new Error("periodDatesRequired");
  }
  const [year, month, day] = dateKey.split("-").map(Number);
  return {year, month, day};
}

function isValidDateKey(dateKey) {
  if (!dateKeyPattern.test(dateKey || "")) {
    return false;
  }
  const [year, month, day] = dateKey.split("-").map(Number);
  const value = new Date(Date.UTC(year, month - 1, day));
  return value.getUTCFullYear() === year && value.getUTCMonth() === month - 1 && value.getUTCDate() === day;
}

function formatDateKey(year, month, day) {
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}
