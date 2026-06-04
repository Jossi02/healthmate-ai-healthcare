'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import { HeartPulse, Loader2, ArrowRight } from 'lucide-react';
import {
  AUTH_TOKEN_STORAGE_KEY,
  AUTH_USER_STORAGE_KEY,
  clearClientAuthState,
} from '@/lib/auth';
import { usePlan } from '../context/PlanContext';

export default function LoginPage() {
  const router = useRouter();
  const [loginId, setLoginId] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const { fetchUserProfile } = usePlan();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('reason') === 'session-expired') {
      setErrorMsg('세션이 만료되었어요. 다시 로그인해주세요.');
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!loginId || !password) {
      setErrorMsg('아이디와 비밀번호를 모두 입력해주세요.');
      return;
    }

    setErrorMsg('');
    setIsLoading(true);

    try {
      const rawApiUrl = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || '';
      const baseUrl = rawApiUrl.endsWith('/') ? rawApiUrl.slice(0, -1) : rawApiUrl;
      const endpoint = baseUrl ? `${baseUrl}/api/v1/auth/login` : '/api/v1/auth/login';

      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'ngrok-skip-browser-warning': 'true',
        },
        body: JSON.stringify({
          login_id: loginId,
          password,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || '아이디 또는 비밀번호가 올바르지 않습니다.');
      }

      clearClientAuthState();

      if (data.token) {
        localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, data.token);
      }

      if (data.user) {
        localStorage.setItem(
          AUTH_USER_STORAGE_KEY,
          JSON.stringify({
            user_id: data.user.user_id,
            login_id: data.user.login_id,
            nickname: data.user.nickname,
            email: data.user.email,
          })
        );
      }

      await fetchUserProfile();

      if (data.user && data.user.has_health_profile === false) {
        router.push('/onboarding');
      } else {
        router.push('/');
      }
    } catch (err: unknown) {
      console.error('Login error:', err);
      setErrorMsg(err instanceof Error ? err.message : '로그인 중 오류가 발생했습니다.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-[#f8fafc] p-4 sm:p-6">
      <div className="absolute left-[-10%] top-[-10%] h-96 w-96 animate-pulse rounded-full bg-blue-400 opacity-20 mix-blend-multiply blur-3xl filter" />
      <div
        className="absolute right-[-10%] top-[-10%] h-96 w-96 animate-pulse rounded-full bg-indigo-400 opacity-20 mix-blend-multiply blur-3xl filter"
        style={{ animationDelay: '2s' }}
      />
      <div
        className="absolute bottom-[-20%] left-[20%] h-96 w-96 animate-pulse rounded-full bg-emerald-300 opacity-30 mix-blend-multiply blur-3xl filter"
        style={{ animationDelay: '4s' }}
      />

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: 'easeOut' }}
        className="z-10 w-full max-w-md"
      >
        <div className="rounded-3xl border border-gray-100 bg-white/80 p-8 shadow-[0_8px_30px_rgb(0,0,0,0.08)] backdrop-blur-xl sm:p-10">
          <div className="mb-10 text-center">
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ delay: 0.2, type: 'spring', stiffness: 200 }}
              className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-500 shadow-[0_8px_20px_rgba(37,99,235,0.25)]"
            >
              <HeartPulse className="h-8 w-8 text-white" />
            </motion.div>
            <h1 className="bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-3xl font-extrabold tracking-tight text-transparent">
              Health-mate
            </h1>
            <p className="mt-2 font-medium text-gray-500">당신의 건강 메이트가 되어드릴게요.</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="mb-1.5 block text-sm font-bold text-gray-700" htmlFor="loginId">
                아이디
              </label>
              <input
                id="loginId"
                type="text"
                value={loginId}
                onChange={(e) => setLoginId(e.target.value)}
                placeholder="아이디를 입력해주세요"
                className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3.5 text-gray-900 transition-all duration-200 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/50"
                autoComplete="username"
                disabled={isLoading}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-bold text-gray-700" htmlFor="password">
                비밀번호
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="비밀번호를 입력해주세요"
                className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3.5 text-gray-900 transition-all duration-200 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/50"
                autoComplete="current-password"
                disabled={isLoading}
              />
            </div>

            {errorMsg && (
              <motion.p
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                className="rounded-xl bg-red-50 py-2 text-center text-sm font-medium text-red-500"
              >
                {errorMsg}
              </motion.p>
            )}

            <button
              type="submit"
              disabled={isLoading}
              className="mt-4 flex w-full items-center justify-center space-x-2 rounded-2xl bg-gradient-to-r from-blue-600 to-indigo-600 py-4 font-bold text-white shadow-[0_8px_20px_rgba(37,99,235,0.25)] transition-all duration-300 hover:-translate-y-0.5 hover:shadow-[0_12px_25px_rgba(37,99,235,0.35)] disabled:opacity-70 disabled:hover:translate-y-0 disabled:hover:shadow-none"
            >
              {isLoading ? (
                <>
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <span>로그인 중...</span>
                </>
              ) : (
                <>
                  <span>로그인</span>
                  <ArrowRight className="ml-1 h-4 w-4" />
                </>
              )}
            </button>
          </form>

          <div className="mt-8 text-center text-sm">
            <span className="text-gray-500">아직 회원이 아니신가요? </span>
            <button
              onClick={() => router.push('/signup')}
              className="inline-flex min-h-10 items-center rounded-lg px-1 font-bold text-blue-600 transition-all hover:underline"
            >
              회원가입하기
            </button>
          </div>
        </div>
      </motion.div>
      <div className="relative z-10 mt-12 text-center text-sm font-medium text-gray-400">
        © 2026 Health-mate. All rights reserved.
      </div>
    </div>
  );
}
