"use client";

import { Home, MessageSquare, HeartPulse, User } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { usePlan } from '@/app/context/PlanContext';

export default function BottomNav() {
  const pathname = usePathname();
  const { hasPlanUpdates } = usePlan();
  const hiddenRoutes = new Set(['/onboarding', '/login', '/signup']);

  // 인증/온보딩 페이지에서는 네비게이션 바 숨김
  if (hiddenRoutes.has(pathname)) return null;

  const navItems = [
    { name: '홈', path: '/', icon: Home },
    { name: 'AI 챗봇', path: '/chat', icon: MessageSquare },
    { name: '건강 추천', path: '/recommend', icon: HeartPulse },
    { name: '내 정보', path: '/profile', icon: User },
  ];

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 border-t border-gray-200/80 bg-white/90 shadow-[0_-4px_24px_-8px_rgba(0,0,0,0.08)] backdrop-blur-md transition-all duration-300 md:bottom-0 md:right-auto md:top-0 md:w-24 md:border-r md:border-t-0 md:shadow-[4px_0_24px_-12px_rgba(0,0,0,0.14)]">
      <div className="relative mx-auto flex h-20 max-w-md items-center justify-between px-6 md:h-full md:w-full md:flex-col md:justify-center md:gap-6 md:px-0 md:py-8">
        {navItems.map((item) => {
          const isActive = pathname === item.path;
          const Icon = item.icon;
          
          return (
            <Link 
              key={item.path} 
              href={item.path}
              aria-current={isActive ? 'page' : undefined}
              className="group relative flex h-full w-16 flex-col items-center justify-center md:h-16"
            >
              <div className={`flex flex-col items-center justify-center space-y-1.5 transition-all duration-300 ${isActive ? '-translate-y-1 text-[#2563eb]' : 'text-gray-400 group-hover:text-gray-600 group-hover:-translate-y-0.5'}`}>
                {isActive && (
                  <span className="absolute -top-1 h-1 w-8 rounded-b-full bg-[#2563eb] shadow-[0_2px_8px_rgba(37,99,235,0.4)] md:-left-3 md:top-auto md:h-8 md:w-1 md:rounded-r-full md:rounded-bl-none" />
                )}
                <span className="relative">
                  <Icon className={`w-6 h-6 transition-all duration-300 ${isActive ? 'stroke-[2.5px] scale-110 drop-shadow-sm' : 'stroke-2'}`} />
                  {item.path === '/recommend' && hasPlanUpdates && (
                    <>
                      <span
                        className="absolute -right-1.5 -top-1.5 h-2.5 w-2.5 rounded-full bg-rose-500 ring-2 ring-white"
                        aria-hidden="true"
                      />
                      <span className="sr-only">변경된 플랜 있음</span>
                    </>
                  )}
                </span>
                <span className={`text-[10px] font-bold tracking-wide ${isActive ? 'opacity-100' : 'opacity-70 group-hover:opacity-100'}`}>
                  {item.name}
                </span>
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
