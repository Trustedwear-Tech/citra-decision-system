/**
 * EmailAuthScreen — Email/password login & registration
 * Shown when the backend advertises 'local' as an enabled auth provider.
 */
import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
} from 'react-native';
import { API_CONFIG } from '../../config/config';

const AUTH_BASE = API_CONFIG?.AUTH?.baseUrl || 'http://localhost:7004/api/auth';

export default function EmailAuthScreen({ onAuthSuccess, onSwitchToGoogle, theme = 'dark' }) {
  const [mode, setMode] = useState('login'); // 'login' | 'register' | 'forgot'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');

  const isDark = theme === 'dark';

  // ── API helpers ──────────────────────────────────────────────────

  async function apiCall(endpoint, body) {
    const res = await fetch(`${AUTH_BASE}/local${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || 'Request failed');
    return json;
  }

  // ── Handlers ─────────────────────────────────────────────────────

  async function handleLogin() {
    setError('');
    setInfo('');
    if (!email || !password) { setError('Email and password are required.'); return; }
    setLoading(true);
    try {
      const result = await apiCall('/login', { email, password });
      if (result.success && result.data) {
        onAuthSuccess(result.data);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleRegister() {
    setError('');
    setInfo('');
    if (!email || !password) { setError('Email and password are required.'); return; }
    if (password.length < 8) { setError('Password must be at least 8 characters.'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match.'); return; }
    setLoading(true);
    try {
      const result = await apiCall('/register', { email, password, name, termsAcceptedAt: new Date().toISOString() });
      if (result.success && result.data) {
        onAuthSuccess(result.data);
        setInfo('Account created! Please check your email to verify your address.');
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleForgotPassword() {
    setError('');
    setInfo('');
    if (!email) { setError('Please enter your email address.'); return; }
    setLoading(true);
    try {
      const result = await apiCall('/forgot-password', { email });
      setInfo(result.message || 'If an account exists, a reset link has been sent.');
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  // ── Render ───────────────────────────────────────────────────────

  const colors = {
    bg: isDark ? '#1a1a2e' : '#ffffff',
    card: isDark ? '#16213e' : '#f8f9fa',
    text: isDark ? '#e0e0e0' : '#1a1a1a',
    textSecondary: isDark ? '#a0a0a0' : '#666666',
    inputBg: isDark ? '#0f3460' : '#ffffff',
    inputBorder: isDark ? '#1a5276' : '#d0d0d0',
    primary: '#2563eb',
    errorText: '#ef4444',
    infoText: '#22c55e',
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={[styles.container, { backgroundColor: colors.bg }]} keyboardShouldPersistTaps="handled">
        <View style={[styles.card, { backgroundColor: colors.card }]}>

          <Text style={[styles.title, { color: colors.text }]}>
            {mode === 'login' ? 'Sign In' : mode === 'register' ? 'Create Account' : 'Reset Password'}
          </Text>

          {/* ── Error / Info ── */}
          {!!error && <Text style={[styles.msg, { color: colors.errorText }]}>{error}</Text>}
          {!!info && <Text style={[styles.msg, { color: colors.infoText }]}>{info}</Text>}

          {/* ── Name (register only) ── */}
          {mode === 'register' && (
            <TextInput
              style={[styles.input, { backgroundColor: colors.inputBg, borderColor: colors.inputBorder, color: colors.text }]}
              placeholder="Name (optional)"
              placeholderTextColor={colors.textSecondary}
              value={name}
              onChangeText={setName}
              autoCapitalize="words"
            />
          )}

          {/* ── Email ── */}
          <TextInput
            style={[styles.input, { backgroundColor: colors.inputBg, borderColor: colors.inputBorder, color: colors.text }]}
            placeholder="Email"
            placeholderTextColor={colors.textSecondary}
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            keyboardType="email-address"
            textContentType="emailAddress"
          />

          {/* ── Password ── */}
          {mode !== 'forgot' && (
            <TextInput
              style={[styles.input, { backgroundColor: colors.inputBg, borderColor: colors.inputBorder, color: colors.text }]}
              placeholder="Password"
              placeholderTextColor={colors.textSecondary}
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              textContentType={mode === 'register' ? 'newPassword' : 'password'}
            />
          )}

          {/* ── Confirm password (register) ── */}
          {mode === 'register' && (
            <TextInput
              style={[styles.input, { backgroundColor: colors.inputBg, borderColor: colors.inputBorder, color: colors.text }]}
              placeholder="Confirm Password"
              placeholderTextColor={colors.textSecondary}
              value={confirmPassword}
              onChangeText={setConfirmPassword}
              secureTextEntry
              textContentType="newPassword"
            />
          )}

          {/* ── Submit button ── */}
          <TouchableOpacity
            style={[styles.button, { backgroundColor: colors.primary, opacity: loading ? 0.7 : 1 }]}
            onPress={mode === 'login' ? handleLogin : mode === 'register' ? handleRegister : handleForgotPassword}
            disabled={loading}
          >
            {loading ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.buttonText}>
                {mode === 'login' ? 'Sign In' : mode === 'register' ? 'Create Account' : 'Send Reset Link'}
              </Text>
            )}
          </TouchableOpacity>

          {/* ── Mode toggles ── */}
          <View style={styles.links}>
            {mode === 'login' && (
              <>
                <TouchableOpacity onPress={() => { setMode('forgot'); setError(''); setInfo(''); }}>
                  <Text style={[styles.link, { color: colors.primary }]}>Forgot password?</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={() => { setMode('register'); setError(''); setInfo(''); }}>
                  <Text style={[styles.link, { color: colors.primary }]}>Create an account</Text>
                </TouchableOpacity>
              </>
            )}
            {mode === 'register' && (
              <TouchableOpacity onPress={() => { setMode('login'); setError(''); setInfo(''); }}>
                <Text style={[styles.link, { color: colors.primary }]}>Already have an account? Sign in</Text>
              </TouchableOpacity>
            )}
            {mode === 'forgot' && (
              <TouchableOpacity onPress={() => { setMode('login'); setError(''); setInfo(''); }}>
                <Text style={[styles.link, { color: colors.primary }]}>Back to sign in</Text>
              </TouchableOpacity>
            )}
          </View>

          {/* ── Switch to Google ── */}
          {onSwitchToGoogle && (
            <TouchableOpacity onPress={onSwitchToGoogle} style={styles.switchProvider}>
              <Text style={[styles.switchProviderText, { color: colors.textSecondary }]}>
                Or sign in with Google
              </Text>
            </TouchableOpacity>
          )}
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  card: {
    width: '100%',
    maxWidth: 400,
    borderRadius: 12,
    padding: 28,
  },
  title: {
    fontSize: 24,
    fontWeight: '700',
    marginBottom: 20,
    textAlign: 'center',
  },
  msg: {
    fontSize: 14,
    marginBottom: 12,
    textAlign: 'center',
  },
  input: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 14,
    fontSize: 16,
    marginBottom: 12,
  },
  button: {
    borderRadius: 8,
    padding: 14,
    alignItems: 'center',
    marginTop: 4,
    marginBottom: 12,
  },
  buttonText: {
    color: '#ffffff',
    fontSize: 16,
    fontWeight: '600',
  },
  links: {
    alignItems: 'center',
    gap: 10,
    marginTop: 4,
  },
  link: {
    fontSize: 14,
    fontWeight: '500',
  },
  switchProvider: {
    marginTop: 20,
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: '#e0e0e020',
    alignItems: 'center',
  },
  switchProviderText: {
    fontSize: 14,
  },
});
