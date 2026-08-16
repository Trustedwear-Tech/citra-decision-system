/**
 * AdminUsersScreen — list users in the admin's scope and trigger the
 * delete-user flow.
 *
 * Visibility:
 *   - dept_admin sees users in their dept_ids
 *   - org_admin  sees all users in their org_id
 *   - super_admin sees everyone (server-enforced)
 *
 * Actions per row:
 *   "Delete user" → navigates to /admin/users/:userId/delete which loads
 *   the per-resource picker (DeleteUserScreen).
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  FlatList,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
  StyleSheet,
  Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import AdminUserService from '../../services/AdminUserService';
import EditUserModal from './EditUserModal';


const AdminUsersScreen = ({ navigation }) => {
  const [users, setUsers] = useState([]);
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [editingUser, setEditingUser] = useState(null);

  const load = useCallback(async () => {
    try {
      const list = await AdminUserService.listUsers({ q });
      setUsers(list);
      setError(null);
    } catch (err) {
      setError(err.message || 'failed to load users');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [q]);

  useEffect(() => { load(); }, [load]);

  const onRefresh = () => { setRefreshing(true); load(); };

  const handleDelete = (user) => {
    Alert.alert(
      'Delete user',
      `You are about to delete ${user.email}. Their workflows + smart-apps will be handed off based on your choices on the next screen.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Continue',
          style: 'destructive',
          onPress: () => navigation.navigate('DeleteUserScreen', { userId: user.email }),
        },
      ],
    );
  };

  const renderRow = ({ item }) => {
    const tone = item.deletion_state === 'deleted'
      ? styles.rowDeleted
      : item.deletion_state === 'pending_deletion'
        ? styles.rowPending
        : null;
    return (
      <View style={[styles.row, tone]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.email}>{item.email}</Text>
          <Text style={styles.meta}>
            {(item.roles || []).join(', ') || 'user'}
            {item.dept_ids?.length ? ` · dept: ${item.dept_ids.join(', ')}` : ''}
          </Text>
          {item.deletion_state && item.deletion_state !== 'active' && (
            <Text style={styles.stateBadge}>{item.deletion_state}</Text>
          )}
        </View>
        {item.deletion_state !== 'deleted' && (
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <TouchableOpacity
              style={styles.editBtn}
              onPress={() => setEditingUser(item)}
            >
              <Ionicons name="create-outline" size={18} color="#2563EB" />
              <Text style={styles.editBtnText}>Edit</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.deleteBtn}
              onPress={() => handleDelete(item)}
            >
              <Ionicons name="trash-outline" size={18} color="#c44" />
              <Text style={styles.deleteBtnText}>Delete</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    );
  };

  const handleUserSaved = (updated) => {
    if (!updated || !updated.email) return;
    setUsers((prev) => prev.map((u) => (u.email === updated.email ? { ...u, ...updated } : u)));
  };

  return (
    <View style={styles.container}>
      <View style={styles.searchRow}>
        <Ionicons name="search" size={16} color="#888" />
        <TextInput
          style={styles.searchInput}
          placeholder="Search users by email…"
          value={q}
          onChangeText={setQ}
          onSubmitEditing={load}
          autoCapitalize="none"
        />
      </View>
      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} />
      ) : error ? (
        <Text style={styles.error}>{error}</Text>
      ) : (
        <FlatList
          data={users}
          keyExtractor={(u) => u.email}
          renderItem={renderRow}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
          }
          ListEmptyComponent={
            <Text style={styles.empty}>No users in your scope.</Text>
          }
        />
      )}
      <EditUserModal
        visible={!!editingUser}
        user={editingUser}
        onClose={() => setEditingUser(null)}
        onSaved={handleUserSaved}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    borderBottomWidth: 1,
    borderColor: '#eee',
  },
  searchInput: { flex: 1, marginLeft: 8, fontSize: 14 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    borderBottomWidth: 1,
    borderColor: '#f0f0f0',
  },
  rowPending: { backgroundColor: '#fff8e1' },
  rowDeleted: { backgroundColor: '#fce8e6', opacity: 0.6 },
  email: { fontSize: 15, fontWeight: '500', color: '#222' },
  meta: { fontSize: 12, color: '#666', marginTop: 2 },
  stateBadge: {
    marginTop: 4,
    alignSelf: 'flex-start',
    fontSize: 11,
    backgroundColor: '#eef',
    color: '#226',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  editBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: '#2563EB',
    borderRadius: 6,
    marginRight: 8,
  },
  editBtnText: { color: '#2563EB', marginLeft: 4, fontSize: 13, fontWeight: '500' },
  deleteBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: '#c44',
    borderRadius: 6,
  },
  deleteBtnText: { color: '#c44', marginLeft: 4, fontSize: 13, fontWeight: '500' },
  empty: { textAlign: 'center', marginTop: 40, color: '#888' },
  error: { color: '#c44', padding: 16 },
});

export default AdminUsersScreen;
