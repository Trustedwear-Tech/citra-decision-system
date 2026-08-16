module.exports = {
  preset: 'jest-expo/web',
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['./tests/setup.js'],
  testMatch: ['**/tests/**/*.test.js'],
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native(-web)?|@react-native(-community)?|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|@xyflow/.*|react-native-markdown-display|uuid|firebase|@firebase/.*)/)',
  ],
  moduleNameMapper: {
    '\\.(css)$': '<rootDir>/tests/__mocks__/styleMock.js',
    '^@xyflow/react$': '<rootDir>/tests/__mocks__/xyflow-react.js',
    '^@uiw/react-codemirror$': '<rootDir>/tests/__mocks__/react-codemirror.js',
    '^@codemirror/lang-python$': '<rootDir>/tests/__mocks__/codemirror-lang.js',
    '^@codemirror/lang-json$': '<rootDir>/tests/__mocks__/codemirror-lang.js',
    '^@codemirror/lang-html$': '<rootDir>/tests/__mocks__/codemirror-lang.js',
    '^@codemirror/lang-sql$': '<rootDir>/tests/__mocks__/codemirror-lang.js',
    '^@codemirror/view$': '<rootDir>/tests/__mocks__/codemirror-view.js',
  },
};
