import { Platform } from 'react-native';

export const palette = {
  canvas: '#F5F1E8',
  canvasDeep: '#EDE7DA',
  surface: '#FFFCF5',
  surfaceMuted: '#F0EBDF',
  ink: '#1C2A22',
  inkSoft: '#3B4A41',
  muted: '#5E6B61',
  faint: '#647168',
  line: '#DFD8C8',
  lineStrong: '#C9C0AC',
  accent: '#2E5B43',
  accentPressed: '#244936',
  accentSoft: '#E3ECE3',
  accentInk: '#1F4634',
  sage: '#54745D',
  amber: '#94601F',
  amberSoft: '#F3E7D1',
  danger: '#9B3F34',
  dangerSoft: '#F3E1DD',
  white: '#FFFFFF',
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
};

export const radii = {
  sm: 10,
  md: 14,
  lg: 20,
  xl: 26,
  pill: 999,
};

export const displayFont = Platform.OS === 'android' ? 'serif' : 'Georgia';
