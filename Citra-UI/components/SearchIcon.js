import React from 'react';
import { TouchableOpacity, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const SearchIcon = ({ onPress, size = 28, color = '#fff', style = {} }) => {
  return (
    <TouchableOpacity 
      style={[styles.container, style]} 
      onPress={onPress}
      activeOpacity={0.7}
      accessibilityLabel="Search documents"
      accessibilityHint="Opens document search"
    >
      <Ionicons name="search" size={size} color={color} />
    </TouchableOpacity>
  );
};

const styles = StyleSheet.create({
  container: {
    padding: 8,
    marginHorizontal: 5,
    borderRadius: 6,
  },
});

export default SearchIcon;
