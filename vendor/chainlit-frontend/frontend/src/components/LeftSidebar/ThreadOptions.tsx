import { cn } from '@/lib/utils';
import { Ellipsis, Share2, Star, Trash2 } from 'lucide-react';

import { Pencil } from '@/components/icons/Pencil';
import { buttonVariants } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';

import { Translator } from '../i18n';

interface Props {
  onDelete: () => void;
  onRename: () => void;
  onToggleFavorite?: () => void;
  isFavorite?: boolean;
  onShare?: () => void;
  className?: string;
}

export default function ThreadOptions({
  onDelete,
  onRename,
  onToggleFavorite,
  isFavorite,
  onShare,
  className
}: Props) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <div
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
          }}
          id="thread-options"
          className={cn(
            buttonVariants({ variant: 'ghost', size: 'icon' }),
            'focus:ring-0 focus:ring-offset-0 focus-visible:ring-0 focus-visible:ring-offset-0 text-muted-foreground',
            className
          )}
        >
          <Ellipsis />
        </div>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-44" align="start" forceMount>
        {onToggleFavorite ? (
          <DropdownMenuItem
            id="favorite-thread"
            onClick={(e) => {
              e.stopPropagation();
              onToggleFavorite();
            }}
          >
            {isFavorite ? 'Remove Favorite' : 'Add to Favorites'}
            <Star className="ml-auto h-4 w-4" />
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem
          id="rename-thread"
          onClick={(e) => {
            e.stopPropagation();
            onRename();
          }}
        >
          <Translator path="threadHistory.thread.menu.rename" />
          <Pencil className="ml-auto" />
        </DropdownMenuItem>
        {onShare && (
          <DropdownMenuItem
            id="share-thread"
            onClick={(e) => {
              e.stopPropagation();
              onShare();
            }}
          >
            <Translator path="threadHistory.thread.menu.share" />
            <Share2 className="ml-auto" />
          </DropdownMenuItem>
        )}
        <DropdownMenuItem
          id="delete-thread"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="text-red-500 focus:text-red-500"
        >
          <Translator path="threadHistory.thread.menu.delete" />
          <Trash2 className="ml-auto" />
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
