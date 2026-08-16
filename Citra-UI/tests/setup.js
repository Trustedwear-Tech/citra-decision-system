// Polyfill for jsdom
import '@testing-library/jest-dom';

// Mock expo-constants
jest.mock('expo-constants', () => ({
  default: { expoConfig: { extra: {} } },
}));

// Mock @expo/vector-icons
jest.mock('@expo/vector-icons', () => {
  const React = require('react');
  const MockIcon = (props) => React.createElement('span', { 'data-testid': props.testID || `icon-${props.name}` }, props.name);
  return {
    Ionicons: MockIcon,
    MaterialIcons: MockIcon,
    FontAwesome: MockIcon,
  };
});

// Mock authService used by WorkflowService
jest.mock('../services/authService', () => ({
  default: {
    authenticatedFetch: jest.fn(),
  },
}));

// Silence console.error/warn in tests unless debugging
const originalError = console.error;
const originalWarn = console.warn;
beforeAll(() => {
  console.error = (...args) => {
    if (typeof args[0] === 'string' && args[0].includes('act(')) return;
    originalError.call(console, ...args);
  };
  console.warn = (...args) => {
    if (typeof args[0] === 'string' && args[0].includes('deprecated')) return;
    originalWarn.call(console, ...args);
  };
});
afterAll(() => {
  console.error = originalError;
  console.warn = originalWarn;
});
