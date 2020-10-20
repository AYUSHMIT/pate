{-# LANGUAGE GADTs #-}
{-# LANGUAGE RankNTypes #-}
{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE StandaloneDeriving #-}
{-# LANGUAGE FlexibleContexts #-}
{-# LANGUAGE ConstraintKinds #-}
{-# LANGUAGE UndecidableInstances #-}
{-# LANGUAGE GeneralizedNewtypeDeriving #-}
{-# LANGUAGE TypeApplications #-}
{-# LANGUAGE TypeOperators #-}

module Pate.Monad
  ( EquivEnv(..)
  , EquivState(..)
  , EquivM
  , EquivM_
  , runEquivM
  , ValidSym
  , ValidSolver
  , ValidArch(..)
  , EquivalenceContext(..)
  , BinaryContext(..)
  , withBinary
  , withValid
  , withValidEnv
  , withSymIO
  , withSym
  , withProc
  , archFuns
  , runInIO1
  , errorFrame
  , manifestError
  , implicitError
  , throwHere
  , emitEvent
  )
  where

import           Prelude hiding ( fail )
import           GHC.Stack

import           Control.Monad.Fail
import qualified Control.Monad.IO.Unlift as IO
import           Control.Exception
import           Control.Monad.ST
import           Control.Monad.Trans.Reader ( ReaderT, runReaderT )
import           Control.Monad.Reader
import           Control.Monad.Trans.Except
import           Control.Monad.Except
import           Control.Monad.Trans.State ( StateT, runStateT, evalStateT )
import           Control.Monad.State

import           Data.Set (Set)
import qualified Data.Set as Set

import           Data.Typeable
import qualified Data.ElfEdit as E

import qualified Data.Parameterized.Nonce as N
import           Data.Parameterized.Classes

import qualified Lumberjack as LJ

import qualified Lang.Crucible.LLVM.MemModel as CLM
import qualified Lang.Crucible.Types as CT
import qualified Lang.Crucible.Backend as CB
import qualified Lang.Crucible.Backend.Online as CBO
import qualified Lang.Crucible.Simulator as CS
import qualified Lang.Crucible.Simulator.GlobalState as CGS
import qualified Lang.Crucible.FunctionHandle as CFH

import qualified Data.Macaw.BinaryLoader as MBL
import qualified Data.Macaw.CFG as MM
import qualified Data.Macaw.Types as MM
import qualified Data.Macaw.Symbolic as MS


import qualified What4.Interface as W4
import qualified What4.Protocol.Online as W4O
import qualified What4.Protocol.SMTWriter as W4W

import qualified Pate.Event as PE
import           Pate.Types
import qualified Pate.Memory.MemTrace as MT

data BinaryContext sym arch = BinaryContext
  { binary :: MBL.LoadedBinary arch (E.Elf (MM.ArchAddrWidth arch))
  , parsedFunctionMap :: ParsedFunctionMap arch
  , binEntry :: MM.ArchSegmentOff arch
  }

data EquivalenceContext sym arch where
  EquivalenceContext ::
    forall sym ids arch scope solver fs.
    (ValidArch arch, ValidSym sym, ValidSolver sym scope solver fs) =>
    { nonces :: N.NonceGenerator (ST RealWorld) ids
    , handles :: CFH.HandleAllocator
    , exprBuilder :: sym
    , originalCtx :: BinaryContext sym arch
    , rewrittenCtx :: BinaryContext sym arch
    } -> EquivalenceContext sym arch

class
  ( Typeable arch
  , MBL.BinaryLoader arch (E.Elf (MM.ArchAddrWidth arch))
  , MS.SymArchConstraints arch
  , MS.GenArchInfo MT.MemTraceK arch
  ) => ValidArch arch where
  funCallStable :: forall tp. MM.ArchReg arch tp -> Bool
  -- ^ True for registers that are stable across function calls
  -- These are assumed equivalent between the initial program states
  funCallArg :: forall tp. MM.ArchReg arch tp -> Bool
  -- ^ True for registers that are used as function call arguments
  -- In addition to the stable registers, these must be proven equivalent
  -- when comparing two program states prior to a function call
  funCallRet :: forall tp. MM.ArchReg arch tp -> Bool
  -- ^ True for registers used for function return values.
  -- These must be proven equivalent when returning from a function
  -- and are assumed initially equivalent at intermediate function blocks
  funCallIP :: forall tp. MM.ArchReg arch tp -> Maybe (tp :~: (MM.BVType (MM.RegAddrWidth (MM.ArchReg arch))))
  -- ^ Registers used to store an instruction pointer (i.e. the link register on PPC)


type ValidSym sym =
  ( W4.IsExprBuilder sym
  , CB.IsSymInterface sym
  , ShowF (W4.SymExpr sym)
  )

type ValidSolver sym scope solver fs =
  (sym ~ CBO.OnlineBackend scope solver fs
  , W4O.OnlineSolver solver
  , W4W.SMTReadWriter solver
  )

data EquivEnv sym arch where
  EquivEnv ::
    forall sym arch scope solver fs.
    (ValidArch arch, ValidSym sym, ValidSolver sym scope solver fs) =>
    { envSym :: sym
    , envWhichBinary :: Maybe WhichBinary
    , envProc :: W4O.SolverProcess scope solver
    , envCtx :: EquivalenceContext sym arch
    , envArchVals :: MS.GenArchVals MT.MemTraceK arch
    , envExtensions :: CS.ExtensionImpl (MS.MacawSimulatorState sym) sym (MS.MacawExt arch)
    , envGlobalMap :: CGS.SymGlobalState sym
    , envStackRegion :: W4.SymNat sym
    , envMemTraceVar :: CS.GlobalVar (MT.MemTrace arch)
    , envInitMem :: MT.MemTraceImpl sym (MM.ArchAddrWidth arch)
    , envExitClassVar :: CS.GlobalVar (MT.ExitClassify arch)
    , envReturnIPVar :: CS.GlobalVar (CT.MaybeType (CLM.LLVMPointerType (MM.ArchAddrWidth arch)))
    , envBlockMapping :: BlockMapping arch
    , envLogger :: LJ.LogAction IO (PE.Event arch)
    , envDiscoveryCfg :: DiscoveryConfig
    } -> EquivEnv sym arch

emitEvent :: PE.Event arch -> EquivM sym arch ()
emitEvent evt = do
  logAction <- asks envLogger
  IO.liftIO $ LJ.writeLog logAction evt

data EquivState sym arch where
  EquivState ::
    { stOpenPairs :: Set (PatchPair arch)
    , stVerifiedPairs :: Set (PatchPair arch)
    , stFailedPairs :: Set (PatchPair arch)
    } -> EquivState sym arch


newtype EquivM_ sym arch a = EquivM { unEQ :: ReaderT (EquivEnv sym arch) (StateT (EquivState sym arch) ((ExceptT (EquivalenceError arch) IO))) a }
  deriving (Functor
           , Applicative
           , Monad
           , IO.MonadIO
           , MonadReader (EquivEnv sym arch)
           , MonadError (EquivalenceError arch)
           , MonadState (EquivState sym arch)
           )

type EquivM sym arch a = (ValidArch arch, ValidSym sym) => EquivM_ sym arch a

withBinary ::
  WhichBinary ->
  EquivM sym arch a ->
  EquivM sym arch a
withBinary wb f = local (\env -> env { envWhichBinary = Just wb }) f

withValid :: forall a sym arch.
  (forall scope solver fs.
    ValidSolver sym scope solver fs =>
    EquivM sym arch a) ->
  EquivM_ sym arch a
withValid f = do
  env <- ask
  withValidEnv env $ f



withValidEnv ::
  EquivEnv sym arch ->
  (forall scope solver fs.
    ValidArch arch =>
    ValidSym sym =>
    ValidSolver sym scope solver fs =>
    a) ->
  a
withValidEnv (EquivEnv {}) f = f

withSym ::
  (sym -> EquivM sym arch a) ->
  EquivM sym arch a
withSym f = withValid $ do
  sym <- asks envSym
  f sym

withProc ::
  ( forall scope solver fs.
    ValidSolver sym scope solver fs =>
    W4O.SolverProcess scope solver ->
   EquivM sym arch a) ->
  EquivM sym arch a
withProc f = withValid $ do
  EquivEnv { envProc = p } <- ask
  f p

withSymIO :: forall sym arch a.
  ( sym -> IO a ) ->
  EquivM sym arch a
withSymIO f = withSym (\sym -> liftIO (f sym))

archFuns :: forall sym arch.
  EquivM sym arch (MS.MacawSymbolicArchFunctions arch)
archFuns = do
  archVals <- asks envArchVals
  return $ MS.archFunctions archVals

-- FIXME: state updates are not preserved
instance forall sym arch. IO.MonadUnliftIO (EquivM_ sym arch) where
  withRunInIO f = withValid $ do
    env <- ask
    st <- get
    catchInIO (f (runEquivM' env st))

catchInIO ::
  forall sym arch a.
  IO a ->
  EquivM sym arch a
catchInIO f =
  (liftIO $ catch (Right <$> f) (\(e :: EquivalenceError arch) -> return $ Left e)) >>= \case
    Left err -> throwError err
    Right result -> return result

runInIO1 ::
  IO.MonadUnliftIO m =>
  (a -> m b) ->
  ((a -> IO b) -> IO b) ->
  m b
runInIO1 f g = IO.withRunInIO $ \runInIO -> g (\a -> runInIO (f a))

runEquivM' ::
  EquivEnv sym arch ->
  EquivState sym arch ->
  EquivM sym arch a ->
  IO a
runEquivM' env st f = withValidEnv env $ (runExceptT $ evalStateT (runReaderT (unEQ f) env) st) >>= \case
  Left err -> throwIO err
  Right result -> return $ result

runEquivM ::
  EquivEnv sym arch ->
  EquivState sym arch ->
  EquivM sym arch a ->
  ExceptT (EquivalenceError arch) IO a
runEquivM env st f = withValidEnv env $ evalStateT (runReaderT (unEQ f) env) st

throwHere ::
  HasCallStack =>
  InnerEquivalenceError arch ->
  EquivM_ sym arch a
throwHere err = do
  wb <- asks envWhichBinary
  throwError $ EquivalenceError
    { errWhichBinary = wb
    , errStackTrace = Just callStack
    , errEquivError = err
    }

instance MonadFail (EquivM_ sym arch) where
  fail msg = throwHere $ EquivCheckFailure $ "Fail: " ++ msg


-- | Wrap the argument in an assumption frame in a way that's safe even if it
-- throws an error.
errorFrame ::
  forall sym arch a.
  EquivM sym arch a ->
  EquivM sym arch a
errorFrame f = withSym $ \sym -> do
  st <- liftIO $ CB.saveAssumptionState sym
  res <- manifestError f
  liftIO $ CB.restoreAssumptionState sym st
  implicitError res

manifestError :: MonadError e m => m a -> m (Either e a)
manifestError act = catchError (Right <$> act) (pure . Left)

implicitError :: MonadError e m => Either e a -> m a
implicitError (Left e) = throwError e
implicitError (Right a) = pure a
