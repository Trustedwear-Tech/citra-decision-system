import 'react-native-get-random-values';

if (typeof crypto === 'undefined') {
  const crypto = {
    getRandomValues: (arr: Uint8Array) => {
      for (let i = 0; i < arr.length; i++) {
        arr[i] = Math.floor(Math.random() * 256);
      }
      return arr;
    },
  };
  Object.defineProperty(window, 'crypto', {
    value: crypto,
    writable: true,
    configurable: true,
    enumerable: true,
  });
}